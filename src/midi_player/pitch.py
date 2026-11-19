from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

from .models import MidiNoteEvent

BEAM_WIDTH = 8
MAX_NOTE_CHOICES = 7
MAX_GROUP_CHOICES = 48


class PitchRemapCancelled(Exception):
    """Raised when a stale UI analysis no longer needs a remap result."""


@dataclass(frozen=True)
class _VoiceAssignment:
    index: int
    voice: str
    rank: float


@dataclass(frozen=True)
class _PitchChoice:
    pitch: int
    cost: float
    shift: int


@dataclass(frozen=True)
class _GroupChoice:
    pitches: tuple[tuple[int, int], ...]
    cost: float


@dataclass(frozen=True)
class _BeamState:
    score: float
    mapped: tuple[tuple[int, int], ...]
    previous_pitch_by_voice: tuple[tuple[str, int], ...]
    previous_original_by_voice: tuple[tuple[str, int], ...]
    previous_shift_by_voice: tuple[tuple[str, int], ...]


def remap_pitch_to_playable_octave(
    pitch: int,
    playable_pitches: set[int] | frozenset[int],
) -> int:
    if not playable_pitches or pitch in playable_pitches:
        return pitch

    candidates = _octave_candidates(pitch, playable_pitches)
    if not candidates:
        return pitch
    return candidates[0]


def remap_note_pitches(
    notes: Sequence[MidiNoteEvent],
    playable_pitches: set[int] | frozenset[int],
    transpose: int = 0,
    group_window: float = 0.08,
    phrase_window: float = 2.0,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, ...]:
    """Compress MIDI pitches into the playable keyboard range.

    This stays intentionally local and deterministic: it builds playable pitch
    candidates for each note, scores them by musical role and distance from the
    source, then uses phrase-level beam search to avoid abrupt octave flips.
    """
    if not notes:
        return ()

    original = [note.pitch + transpose for note in notes]
    if not playable_pitches:
        return tuple(original)

    playable = frozenset(playable_pitches)
    mapped = list(original)
    groups = _time_groups(notes, group_window)
    assignments = _assign_voices(notes, groups, original, phrase_window)
    assignment_by_index = {assignment.index: assignment for assignment in assignments}
    candidate_cache: dict[int, tuple[int, ...]] = {}

    previous_pitch_by_voice: dict[str, int] = {}
    previous_original_by_voice: dict[str, int] = {}
    previous_shift_by_voice: dict[str, int] = {}
    for phrase in _phrase_windows(notes, phrase_window):
        _raise_if_cancelled(should_cancel)
        phrase_groups = [group for group in groups if group[0] in phrase]
        phrase_mapping, context = _compress_phrase(
            notes,
            original,
            phrase_groups,
            assignment_by_index,
            playable,
            previous_pitch_by_voice,
            previous_original_by_voice,
            previous_shift_by_voice,
            candidate_cache,
            should_cancel,
        )
        for index, pitch in phrase_mapping.items():
            mapped[index] = pitch
        previous_pitch_by_voice, previous_original_by_voice, previous_shift_by_voice = context

    for group in groups:
        _raise_if_cancelled(should_cancel)
        _resolve_group_collisions(notes, original, mapped, group, playable)

    return tuple(mapped)


def choose_best_transpose(
    notes: Sequence[MidiNoteEvent],
    playable_pitches: set[int] | frozenset[int],
    min_transpose: int = -24,
    max_transpose: int = 24,
    tolerance_ratio: float = 0.05,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Choose the smallest transpose that puts the most notes in range."""
    if not notes or not playable_pitches:
        return 0

    playable = frozenset(playable_pitches)
    total_notes = len(notes)

    candidates = []
    for transpose in range(min_transpose, max_transpose + 1):
        _raise_if_cancelled(should_cancel)
        playable_count = 0
        distance = 0
        for note in notes:
            pitch = note.pitch + transpose
            if pitch in playable:
                playable_count += 1
                continue
            distance += _distance_to_playable(pitch, playable)

        candidates.append((transpose, playable_count, distance))

    if not candidates:
        return 0

    max_playable = max(c[1] for c in candidates)
    threshold = max_playable - (total_notes * tolerance_ratio)

    def score(candidate):
        transpose, playable_count, distance = candidate
        if playable_count >= threshold:
            return (1, -abs(transpose), playable_count, -distance)
        return (0, playable_count, -abs(transpose), -distance)

    return max(candidates, key=score)[0]


def _time_groups(
    notes: Sequence[MidiNoteEvent],
    group_window: float,
) -> list[list[int]]:
    groups: list[list[int]] = []
    group_start: float | None = None
    group: list[int] = []

    for index, note in enumerate(notes):
        if group_start is None or note.start - group_start <= group_window:
            if group_start is None:
                group_start = note.start
            group.append(index)
            continue

        groups.append(group)
        group_start = note.start
        group = [index]

    if group:
        groups.append(group)
    return groups


def _assign_voices(
    notes: Sequence[MidiNoteEvent],
    groups: Sequence[Sequence[int]],
    pitches: Sequence[int],
    context_window: float,
) -> list[_VoiceAssignment]:
    assignments: list[_VoiceAssignment] = []
    singleton_indices = [group[0] for group in groups if len(group) == 1]
    local_contexts = _local_pitch_contexts(notes, pitches, context_window, singleton_indices)

    for group in groups:
        ordered = sorted(group, key=lambda index: (pitches[index], index))
        count = len(ordered)
        if count == 1:
            index = ordered[0]
            rank, spread = local_contexts[index]
            voice = "solo" if spread < 12 else _voice_from_rank(rank)
            assignments.append(_VoiceAssignment(index, voice, rank))
            continue

        for position, index in enumerate(ordered):
            rank = position / max(1, count - 1)
            if position == 0:
                voice = "bass"
            elif position == count - 1:
                voice = "melody"
            else:
                voice = "inner"
            assignments.append(_VoiceAssignment(index, voice, rank))

    return assignments


def _local_pitch_contexts(
    notes: Sequence[MidiNoteEvent],
    pitches: Sequence[int],
    context_window: float,
    target_indices: Sequence[int],
) -> dict[int, tuple[float, int]]:
    if not target_indices:
        return {}

    target_set = set(target_indices)
    contexts: dict[int, tuple[float, int]] = {}
    ordered_indices = sorted(range(len(notes)), key=lambda index: (notes[index].start, index))
    counts: dict[int, int] = {}
    left = 0
    right = 0
    total = 0

    def add_pitch(pitch: int) -> None:
        nonlocal total
        counts[pitch] = counts.get(pitch, 0) + 1
        total += 1

    def remove_pitch(pitch: int) -> None:
        nonlocal total
        next_count = counts[pitch] - 1
        if next_count:
            counts[pitch] = next_count
        else:
            counts.pop(pitch)
        total -= 1

    for index in ordered_indices:
        start = notes[index].start
        while right < len(ordered_indices) and notes[ordered_indices[right]].start <= (
            start + context_window
        ):
            add_pitch(pitches[ordered_indices[right]])
            right += 1
        while left < right and notes[ordered_indices[left]].start < start - context_window:
            remove_pitch(pitches[ordered_indices[left]])
            left += 1

        if index not in target_set:
            continue
        if total <= 1:
            contexts[index] = (0.5, 0)
            continue

        pitch = pitches[index]
        lower = sum(count for value, count in counts.items() if value < pitch)
        equal = counts.get(pitch, 0)
        rank = (lower + (equal - 1) / 2) / max(1, total - 1)
        contexts[index] = (rank, max(counts) - min(counts))

    return contexts


def _voice_from_rank(rank: float) -> str:
    if rank <= 0.45:
        return "bass"
    if rank >= 0.55:
        return "melody"
    return "inner"


def _phrase_windows(
    notes: Sequence[MidiNoteEvent],
    phrase_window: float,
) -> list[set[int]]:
    windows: list[set[int]] = []
    window_start: float | None = None
    window: set[int] = set()

    for index, note in enumerate(notes):
        if window_start is None or note.start - window_start <= phrase_window:
            if window_start is None:
                window_start = note.start
            window.add(index)
            continue

        windows.append(window)
        window_start = note.start
        window = {index}

    if window:
        windows.append(window)
    return windows


def _compress_phrase(
    notes: Sequence[MidiNoteEvent],
    original: Sequence[int],
    groups: Sequence[Sequence[int]],
    assignment_by_index: dict[int, _VoiceAssignment],
    playable_pitches: frozenset[int],
    previous_pitch_by_voice: dict[str, int],
    previous_original_by_voice: dict[str, int],
    previous_shift_by_voice: dict[str, int],
    candidate_cache: dict[int, tuple[int, ...]],
    should_cancel: Callable[[], bool] | None,
) -> tuple[dict[int, int], tuple[dict[str, int], dict[str, int], dict[str, int]]]:
    if not groups:
        return {}, (previous_pitch_by_voice, previous_original_by_voice, previous_shift_by_voice)

    beam = [
        _BeamState(
            score=0.0,
            mapped=(),
            previous_pitch_by_voice=_freeze_str_int_map(previous_pitch_by_voice),
            previous_original_by_voice=_freeze_str_int_map(previous_original_by_voice),
            previous_shift_by_voice=_freeze_str_int_map(previous_shift_by_voice),
        )
    ]

    for group in groups:
        _raise_if_cancelled(should_cancel)
        expanded: list[_BeamState] = []
        for state in beam:
            state_previous_pitch = dict(state.previous_pitch_by_voice)
            state_previous_original = dict(state.previous_original_by_voice)
            state_previous_shift = dict(state.previous_shift_by_voice)
            group_choices = _group_mapping_candidates(
                notes,
                original,
                group,
                assignment_by_index,
                playable_pitches,
                state_previous_pitch,
                state_previous_original,
                state_previous_shift,
                candidate_cache,
                should_cancel,
            )

            for group_choice in group_choices:
                mapped = dict(state.mapped)
                next_previous_pitch = dict(state_previous_pitch)
                next_previous_original = dict(state_previous_original)
                next_previous_shift = dict(state_previous_shift)
                for index, pitch in group_choice.pitches:
                    assignment = assignment_by_index[index]
                    mapped[index] = pitch
                    next_previous_pitch[assignment.voice] = pitch
                    next_previous_original[assignment.voice] = original[index]
                    next_previous_shift[assignment.voice] = pitch - original[index]

                expanded.append(
                    _BeamState(
                        score=state.score + group_choice.cost,
                        mapped=_freeze_int_int_map(mapped),
                        previous_pitch_by_voice=_freeze_str_int_map(next_previous_pitch),
                        previous_original_by_voice=_freeze_str_int_map(next_previous_original),
                        previous_shift_by_voice=_freeze_str_int_map(next_previous_shift),
                    )
                )

        if not expanded:
            break
        beam = sorted(expanded, key=lambda item: item.score)[:BEAM_WIDTH]

    if not beam:
        return {}, (previous_pitch_by_voice, previous_original_by_voice, previous_shift_by_voice)

    best = min(beam, key=lambda item: item.score)
    return dict(best.mapped), (
        dict(best.previous_pitch_by_voice),
        dict(best.previous_original_by_voice),
        dict(best.previous_shift_by_voice),
    )


def _group_mapping_candidates(
    notes: Sequence[MidiNoteEvent],
    original: Sequence[int],
    indices: Sequence[int],
    assignment_by_index: dict[int, _VoiceAssignment],
    playable_pitches: frozenset[int],
    previous_pitch_by_voice: dict[str, int],
    previous_original_by_voice: dict[str, int],
    previous_shift_by_voice: dict[str, int],
    candidate_cache: dict[int, tuple[int, ...]],
    should_cancel: Callable[[], bool] | None,
) -> list[_GroupChoice]:
    ordered = sorted(
        indices,
        key=lambda index: (assignment_by_index[index].rank, original[index], index),
    )
    partials: list[tuple[float, tuple[tuple[int, int], ...]]] = [(0.0, ())]

    for index in ordered:
        _raise_if_cancelled(should_cancel)
        assignment = assignment_by_index[index]
        choices = _note_pitch_choices(
            notes[index],
            original[index],
            assignment,
            playable_pitches,
            previous_pitch_by_voice,
            previous_original_by_voice,
            previous_shift_by_voice,
            candidate_cache,
        )
        next_partials: list[tuple[float, tuple[tuple[int, int], ...]]] = []
        for score, pairs in partials:
            for choice in choices:
                collision_cost = _collision_cost(notes, index, choice.pitch, pairs)
                next_partials.append(
                    (
                        score + choice.cost + collision_cost,
                        pairs + ((index, choice.pitch),),
                    )
                )
        partials = sorted(next_partials, key=lambda item: item[0])[:MAX_GROUP_CHOICES]

    group_choices = [
        _GroupChoice(
            pitches=_freeze_int_int_map(dict(pairs)),
            cost=score + _group_vertical_cost(notes, original, pairs),
        )
        for score, pairs in partials
    ]
    return sorted(group_choices, key=lambda item: item.cost)[:MAX_GROUP_CHOICES]


def _note_pitch_choices(
    note: MidiNoteEvent,
    original: int,
    assignment: _VoiceAssignment,
    playable_pitches: frozenset[int],
    previous_pitch_by_voice: dict[str, int],
    previous_original_by_voice: dict[str, int],
    previous_shift_by_voice: dict[str, int],
    candidate_cache: dict[int, tuple[int, ...]],
) -> list[_PitchChoice]:
    min_playable = min(playable_pitches)
    max_playable = max(playable_pitches)
    playable_span = max(1, max_playable - min_playable)
    octave_matches = _octave_candidates(original, playable_pitches)

    choices: list[_PitchChoice] = []
    for candidate in _playable_pitch_candidates(original, playable_pitches, candidate_cache):
        shift = candidate - original
        cost = _voice_candidate_cost(
            original,
            candidate,
            assignment,
            min_playable,
            max_playable,
            playable_span,
        )
        cost += _pitch_mutation_cost(original, candidate, assignment, bool(octave_matches))
        cost += _note_importance_cost(note, original, candidate, assignment)
        cost += _voice_continuity_cost(
            original,
            candidate,
            assignment,
            previous_pitch_by_voice,
            previous_original_by_voice,
            previous_shift_by_voice,
        )
        if candidate == original:
            cost -= 8.0
        choices.append(_PitchChoice(candidate, cost, shift))

    if not choices:
        return [_PitchChoice(original, 0.0, 0)]
    return sorted(choices, key=lambda item: (item.cost, abs(item.shift), item.pitch))[
        :MAX_NOTE_CHOICES
    ]


def _playable_pitch_candidates(
    pitch: int,
    playable_pitches: frozenset[int],
    candidate_cache: dict[int, tuple[int, ...]] | None = None,
) -> list[int]:
    if candidate_cache is not None:
        cached = candidate_cache.get(pitch)
        if cached is not None:
            return list(cached)

    playable = set(playable_pitches)
    candidates = set(_octave_candidates(pitch, playable_pitches))

    for octave in range(-6, 7):
        anchor = pitch + octave * 12
        candidates.update(
            _nearest_playable_pitches(
                anchor,
                playable_pitches,
                max_distance=2,
                limit=2,
            )
        )

    if not candidates:
        candidates.update(_nearest_playable_pitches(pitch, playable_pitches, limit=4))

    center = (min(playable) + max(playable)) / 2
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _pitch_class_distance(pitch, candidate),
            abs(candidate - pitch),
            abs(candidate - center),
            candidate,
        ),
    )[:MAX_NOTE_CHOICES]
    if candidate_cache is not None:
        candidate_cache[pitch] = tuple(ranked)
    return ranked


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise PitchRemapCancelled()


def _nearest_playable_pitches(
    pitch: int,
    playable_pitches: frozenset[int],
    *,
    max_distance: int | None = None,
    limit: int = 2,
) -> list[int]:
    ranked = sorted(
        playable_pitches,
        key=lambda candidate: (abs(candidate - pitch), candidate),
    )
    if max_distance is not None:
        ranked = [candidate for candidate in ranked if abs(candidate - pitch) <= max_distance]
    return ranked[:limit]


def _pitch_mutation_cost(
    original: int,
    candidate: int,
    assignment: _VoiceAssignment,
    has_octave_match: bool,
) -> float:
    pitch_class_distance = _pitch_class_distance(original, candidate)
    if pitch_class_distance == 0:
        return 0.0

    voice_weight = 14.0 if assignment.voice in {"melody", "solo"} else 9.0
    cost = pitch_class_distance * voice_weight
    if has_octave_match:
        cost += 22.0
    return cost


def _note_importance_cost(
    note: MidiNoteEvent,
    original: int,
    candidate: int,
    assignment: _VoiceAssignment,
) -> float:
    if candidate == original:
        return 0.0

    cost = 0.0
    if assignment.voice in {"melody", "bass"}:
        cost += 3.0
    if note.duration >= 0.45:
        cost += 2.5
    if note.velocity >= 90:
        cost += 2.0
    return cost


def _voice_continuity_cost(
    original: int,
    candidate: int,
    assignment: _VoiceAssignment,
    previous_pitch_by_voice: dict[str, int],
    previous_original_by_voice: dict[str, int],
    previous_shift_by_voice: dict[str, int],
) -> float:
    voice = assignment.voice
    cost = 0.0
    previous_shift = previous_shift_by_voice.get(voice)
    if previous_shift is not None:
        cost += abs((candidate - original) - previous_shift) * 1.4

    previous_pitch = previous_pitch_by_voice.get(voice)
    previous_original = previous_original_by_voice.get(voice)
    if previous_pitch is None or previous_original is None:
        return cost

    source_interval = original - previous_original
    mapped_interval = candidate - previous_pitch
    cost += abs(mapped_interval - source_interval) * 0.75
    cost += max(0, abs(mapped_interval) - 12) * 0.9
    if source_interval * mapped_interval < 0 and abs(source_interval) > 1:
        cost += 8.0
    return cost


def _collision_cost(
    notes: Sequence[MidiNoteEvent],
    index: int,
    candidate: int,
    pairs: Sequence[tuple[int, int]],
) -> float:
    cost = 0.0
    for previous_index, previous_pitch in pairs:
        if previous_pitch != candidate:
            continue
        if _same_note_class(notes[index], notes[previous_index]):
            continue
        cost += 36.0
    return cost


def _group_vertical_cost(
    notes: Sequence[MidiNoteEvent],
    original: Sequence[int],
    pairs: Sequence[tuple[int, int]],
) -> float:
    if len(pairs) < 2:
        return 0.0

    by_original = sorted(pairs, key=lambda item: (original[item[0]], item[0]))
    cost = 0.0
    for (lower_index, lower_pitch), (upper_index, upper_pitch) in zip(
        by_original,
        by_original[1:],
    ):
        if lower_pitch > upper_pitch:
            cost += (lower_pitch - upper_pitch + 1) * 16.0
        elif lower_pitch == upper_pitch and not _same_note_class(
            notes[lower_index],
            notes[upper_index],
        ):
            cost += 24.0

    original_span = original[by_original[-1][0]] - original[by_original[0][0]]
    mapped_pitches = [pitch for _, pitch in by_original]
    mapped_span = max(mapped_pitches) - min(mapped_pitches)
    if original_span >= 12 and mapped_span < 7:
        cost += (7 - mapped_span) * 2.0
    return cost


def _voice_candidate_cost(
    original: int,
    candidate: int,
    assignment: _VoiceAssignment,
    min_playable: int,
    max_playable: int,
    playable_span: int,
) -> float:
    if assignment.voice == "bass":
        target = min_playable + playable_span * 0.18
    elif assignment.voice == "melody":
        target = min_playable + playable_span * 0.82
    elif assignment.voice == "inner":
        target = min_playable + playable_span * 0.5
    else:
        target = min(max(original, min_playable), max_playable)

    cost = abs(candidate - target) * 1.6
    cost += abs(candidate - original) * 0.7
    if candidate < min_playable:
        cost += (min_playable - candidate) * 40.0
    elif candidate > max_playable:
        cost += (candidate - max_playable) * 40.0

    return cost


def _resolve_group_collisions(
    notes: Sequence[MidiNoteEvent],
    original: Sequence[int],
    mapped: list[int],
    indices: Sequence[int],
    playable_pitches: frozenset[int],
) -> None:
    used: dict[int, int] = {}
    for index in sorted(indices, key=lambda item: (mapped[item], item)):
        pitch = mapped[index]
        previous_index = used.get(pitch)
        if previous_index is None:
            used[pitch] = index
            continue

        if _same_note_class(notes[index], notes[previous_index]):
            continue

        replacement = _first_unused_choice(
            _octave_candidates(original[index], playable_pitches),
            used,
            exclude={pitch},
        )
        previous_replacement = _first_unused_choice(
            _octave_candidates(original[previous_index], playable_pitches),
            used,
            exclude={pitch},
        )

        current_is_originally_playable = original[index] in playable_pitches
        previous_is_originally_playable = original[previous_index] in playable_pitches

        if replacement is not None and not current_is_originally_playable:
            mapped[index] = replacement
            used[replacement] = index
        elif previous_replacement is not None and not previous_is_originally_playable:
            mapped[previous_index] = previous_replacement
            used[previous_replacement] = previous_index
            used[pitch] = index


def _octave_candidates(
    pitch: int,
    playable_pitches: set[int] | frozenset[int],
) -> list[int]:
    playable = set(playable_pitches)
    center = (min(playable) + max(playable)) / 2
    candidates = [pitch + octave * 12 for octave in range(-6, 7) if pitch + octave * 12 in playable]
    return sorted(
        candidates, key=lambda candidate: (abs(candidate - pitch), abs(candidate - center))
    )


def _pitch_class_distance(left: int, right: int) -> int:
    distance = abs(left - right) % 12
    return min(distance, 12 - distance)


def _freeze_int_int_map(values: dict[int, int]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(values.items()))


def _freeze_str_int_map(values: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(values.items()))


def _first_unused_choice(
    choices: list[int],
    used: dict[int, int],
    exclude: set[int] | None = None,
) -> int | None:
    excluded = exclude or set()
    for choice in choices:
        if choice not in used and choice not in excluded:
            return choice
    return None


def _distance_to_playable(
    pitch: int,
    playable_pitches: frozenset[int],
) -> int:
    if pitch in playable_pitches:
        return 0
    min_playable = min(playable_pitches)
    max_playable = max(playable_pitches)
    if pitch < min_playable:
        return min_playable - pitch
    if pitch > max_playable:
        return pitch - max_playable
    return 1


def _same_note_class(left: MidiNoteEvent, right: MidiNoteEvent) -> bool:
    return left.track_index == right.track_index and left.pitch == right.pitch

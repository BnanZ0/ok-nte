from __future__ import annotations

import asyncio
import atexit
import os
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from .cache import MappedPitchCache, MappedPitchCacheKey
from .layout import PianoLayout
from .library import MidiLibraryService
from .models import MidiNoteEvent, ParsedSong, PlaybackOptions, SongInfo
from .parser import parse_midi_file
from .pitch import choose_best_transpose, remap_note_pitches

_PROCESS_POOL_LOCK = Lock()
_PROCESS_POOL: ProcessPoolExecutor | None = None


@dataclass(frozen=True)
class PreparedMidiPlayback:
    song_id: str
    parsed_song: ParsedSong
    notes: tuple[MidiNoteEvent, ...]
    mapped_pitches: tuple[int, ...]
    source_duration: float
    layout: PianoLayout


@dataclass(frozen=True)
class PreparedMidiAnalysis:
    prepared: PreparedMidiPlayback
    applied_transpose: int | None


def prepare_midi_playback(
    parsed_song: ParsedSong,
    layout: PianoLayout,
    transpose: int,
    track_indices: tuple[int, ...] | None = None,
    smart_remap: bool = True,
    mapped_pitch_cache: MappedPitchCache | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> PreparedMidiPlayback:
    notes = parsed_song.notes_for_tracks(track_indices)
    source_duration = _duration_for_notes(notes)
    playable_pitches = layout.playable_pitches
    cache_key = mapped_pitch_cache_key(
        parsed_song.info,
        track_indices,
        playable_pitches,
        transpose,
        smart_remap,
    )

    mapped_pitches = mapped_pitch_cache.get(cache_key) if mapped_pitch_cache else None
    if mapped_pitches is None:
        mapped_pitches = (
            remap_note_pitches(
                notes,
                playable_pitches,
                transpose,
                should_cancel=should_cancel,
            )
            if smart_remap
            else tuple(note.pitch + transpose for note in notes)
        )
        if mapped_pitch_cache is not None:
            mapped_pitch_cache.put(cache_key, mapped_pitches)

    return PreparedMidiPlayback(
        song_id=parsed_song.info.id,
        parsed_song=parsed_song,
        notes=notes,
        mapped_pitches=mapped_pitches,
        source_duration=source_duration,
        layout=layout,
    )


async def prepare_midi_playback_async(
    library: MidiLibraryService,
    song_id: str,
    options: PlaybackOptions,
) -> PreparedMidiPlayback:
    layout = _layout_from_options(options)
    result = await prepare_midi_analysis_async(
        library,
        song_id,
        layout,
        options.transpose,
        options.track_indices,
        options.smart_remap,
        auto_pitch=False,
    )
    return result.prepared


async def prepare_midi_analysis_async(
    library: MidiLibraryService,
    song_id: str,
    layout: PianoLayout,
    transpose: int,
    track_indices: tuple[int, ...] | None = None,
    smart_remap: bool = True,
    *,
    auto_pitch: bool = False,
    min_transpose: int = -24,
    max_transpose: int = 24,
) -> PreparedMidiAnalysis:
    info = library.song_info(song_id)
    cached_pitches = None
    if not auto_pitch:
        cached_pitches = _get_cached_mapped_pitches(
            library,
            info,
            track_indices,
            layout.playable_pitches,
            transpose,
            smart_remap,
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        midi_process_executor(),
        _prepare_midi_analysis_process,
        info,
        layout,
        transpose,
        track_indices,
        smart_remap,
        cached_pitches,
        auto_pitch,
        min_transpose,
        max_transpose,
    )
    _store_prepared_result(library, result, track_indices, smart_remap, transpose)
    return result


def submit_midi_analysis(
    library: MidiLibraryService,
    song_id: str,
    layout: PianoLayout,
    transpose: int,
    track_indices: tuple[int, ...] | None = None,
    smart_remap: bool = True,
    *,
    auto_pitch: bool = False,
    min_transpose: int = -24,
    max_transpose: int = 24,
) -> Future[PreparedMidiAnalysis]:
    info = library.song_info(song_id)
    cached_pitches = None
    if not auto_pitch:
        cached_pitches = _get_cached_mapped_pitches(
            library,
            info,
            track_indices,
            layout.playable_pitches,
            transpose,
            smart_remap,
        )
    return midi_process_executor().submit(
        _prepare_midi_analysis_process,
        info,
        layout,
        transpose,
        track_indices,
        smart_remap,
        cached_pitches,
        auto_pitch,
        min_transpose,
        max_transpose,
    )


def store_prepared_analysis(
    library: MidiLibraryService,
    analysis: PreparedMidiAnalysis,
    track_indices: tuple[int, ...] | None,
    smart_remap: bool,
    transpose: int,
) -> None:
    _store_prepared_result(library, analysis, track_indices, smart_remap, transpose)


def midi_process_executor() -> ProcessPoolExecutor:
    global _PROCESS_POOL
    with _PROCESS_POOL_LOCK:
        if _PROCESS_POOL is None:
            worker_count = max(1, min(2, (os.cpu_count() or 2) - 1))
            _PROCESS_POOL = ProcessPoolExecutor(
                max_workers=worker_count,
            )
            atexit.register(_shutdown_midi_process_executor)
        return _PROCESS_POOL


def _shutdown_midi_process_executor() -> None:
    global _PROCESS_POOL
    with _PROCESS_POOL_LOCK:
        executor = _PROCESS_POOL
        _PROCESS_POOL = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _prepare_midi_analysis_process(
    info: SongInfo,
    layout: PianoLayout,
    transpose: int,
    track_indices: tuple[int, ...] | None,
    smart_remap: bool,
    cached_pitches: tuple[int, ...] | None,
    auto_pitch: bool,
    min_transpose: int,
    max_transpose: int,
) -> PreparedMidiAnalysis:
    parsed = parse_midi_file(info)
    notes = parsed.notes_for_tracks(track_indices)
    source_duration = _duration_for_notes(notes)
    applied_transpose = None
    analysis_transpose = transpose
    if auto_pitch:
        applied_transpose = choose_best_transpose(
            notes,
            layout.playable_pitches,
            min_transpose,
            max_transpose,
        )
        analysis_transpose = applied_transpose

    mapped_pitches = cached_pitches
    if mapped_pitches is None or len(mapped_pitches) != len(notes):
        mapped_pitches = (
            remap_note_pitches(
                notes,
                layout.playable_pitches,
                analysis_transpose,
            )
            if smart_remap
            else tuple(note.pitch + analysis_transpose for note in notes)
        )

    return PreparedMidiAnalysis(
        prepared=PreparedMidiPlayback(
            song_id=parsed.info.id,
            parsed_song=parsed,
            notes=notes,
            mapped_pitches=mapped_pitches,
            source_duration=source_duration,
            layout=layout,
        ),
        applied_transpose=applied_transpose,
    )


def _get_cached_mapped_pitches(
    library: MidiLibraryService,
    info: SongInfo,
    track_indices: tuple[int, ...] | None,
    playable_pitches: frozenset[int],
    transpose: int,
    smart_remap: bool,
) -> tuple[int, ...] | None:
    cache_key = mapped_pitch_cache_key(
        info,
        track_indices,
        playable_pitches,
        transpose,
        smart_remap,
    )
    return library.mapped_pitch_cache.get(cache_key)


def _store_prepared_result(
    library: MidiLibraryService,
    analysis: PreparedMidiAnalysis,
    track_indices: tuple[int, ...] | None,
    smart_remap: bool,
    transpose: int,
) -> None:
    prepared = analysis.prepared
    library.cache_parsed_song(prepared.parsed_song)
    analysis_transpose = (
        analysis.applied_transpose if analysis.applied_transpose is not None else transpose
    )
    cache_key = mapped_pitch_cache_key(
        prepared.parsed_song.info,
        track_indices,
        prepared.layout.playable_pitches,
        analysis_transpose,
        smart_remap,
    )
    library.mapped_pitch_cache.put(cache_key, prepared.mapped_pitches)


def _layout_from_options(options: PlaybackOptions) -> PianoLayout:
    if options.bounds is None:
        return PianoLayout.default(options.layout_mode)
    return PianoLayout(options.layout_mode, options.bounds)


def mapped_pitch_cache_key(
    song_info: SongInfo,
    track_indices: tuple[int, ...] | None,
    playable_pitches: frozenset[int],
    transpose: int,
    smart_remap: bool,
) -> MappedPitchCacheKey:
    normalized_tracks = None
    if track_indices is not None:
        normalized_tracks = tuple(sorted(set(track_indices)))
    return MappedPitchCacheKey(
        song_id=song_info.id,
        mtime=song_info.mtime,
        size=song_info.size,
        track_indices=normalized_tracks,
        playable_pitches=tuple(sorted(playable_pitches)),
        transpose=int(transpose),
        smart_remap=bool(smart_remap),
    )


def _mapped_pitch_cache_key(
    parsed_song: ParsedSong,
    track_indices: tuple[int, ...] | None,
    playable_pitches: frozenset[int],
    transpose: int,
    smart_remap: bool,
) -> MappedPitchCacheKey:
    return mapped_pitch_cache_key(
        parsed_song.info,
        track_indices,
        playable_pitches,
        transpose,
        smart_remap,
    )


def _duration_for_notes(notes: tuple[MidiNoteEvent, ...]) -> float:
    if not notes:
        return 0.0
    return max(note.start + note.duration for note in notes)

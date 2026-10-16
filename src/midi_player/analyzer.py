from __future__ import annotations

from .layout import PianoLayout
from .models import Instrument, LayoutMode, ParsedSong, SongStats
from .preparation import prepare_midi_playback


class MidiAnalyzer:
    def analyze(
        self,
        parsed_song: ParsedSong,
        layout_mode: LayoutMode | str,
        transpose: int = 0,
        bounds: tuple[float, float, float, float] | None = None,
        track_indices: tuple[int, ...] | None = None,
        smart_remap: bool = True,
        *,
        instrument: Instrument | str = Instrument.PIANO,
    ) -> SongStats:
        if bounds is None:
            layout = PianoLayout.default(layout_mode, instrument=instrument)
        else:
            layout = PianoLayout(LayoutMode(layout_mode), bounds, instrument=instrument)

        playable = layout.playable_pitches
        playable_count = 0
        playable_pitches: set[int] = set()
        unplayable_pitches: set[int] = set()

        prepared = prepare_midi_playback(
            parsed_song,
            layout,
            transpose,
            track_indices,
            smart_remap,
        )
        pitches = prepared.mapped_pitches
        for pitch in pitches:
            if pitch in playable:
                playable_count += 1
                playable_pitches.add(pitch)
            else:
                unplayable_pitches.add(pitch)

        total = len(prepared.notes)
        return SongStats(
            total_notes=total,
            playable_notes=playable_count,
            unplayable_notes=total - playable_count,
            playable_pitches=tuple(sorted(playable_pitches)),
            unplayable_pitches=tuple(sorted(unplayable_pitches)),
        )

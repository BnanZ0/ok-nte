from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

DEFAULT_36_KEY_BOUNDS = (0.103, 0.654, 0.903, 0.919)
DEFAULT_21_KEY_BOUNDS = (0.247, 0.655, 0.802, 0.918)


class LayoutMode(str, Enum):
    KEYS_36 = "36"
    KEYS_21 = "21"


class Instrument(str, Enum):
    PIANO = "piano"
    GUITAR = "guitar"


class PlayMode(str, Enum):
    SINGLE_LOOP = "single_loop"
    SEQUENTIAL = "sequential"
    RANDOM = "random"


@dataclass(frozen=True)
class SongInfo:
    id: str
    title: str
    path: Path
    size: int
    mtime: float
    favorite: bool = False


@dataclass(frozen=True)
class MidiNoteEvent:
    pitch: int
    start: float
    duration: float
    velocity: int = 64
    track_index: int = 0


@dataclass(frozen=True)
class MidiTrackInfo:
    index: int
    name: str
    note_count: int


@dataclass(frozen=True)
class ParsedSong:
    info: SongInfo
    duration: float
    ticks_per_beat: int
    notes: tuple[MidiNoteEvent, ...]
    tracks: tuple[MidiTrackInfo, ...] = ()

    def notes_for_tracks(
        self, track_indices: tuple[int, ...] | None = None
    ) -> tuple[MidiNoteEvent, ...]:
        if track_indices is None:
            return self.notes
        selected = set(track_indices)
        return tuple(note for note in self.notes if note.track_index in selected)

    def duration_for_tracks(self, track_indices: tuple[int, ...] | None = None) -> float:
        notes = self.notes_for_tracks(track_indices)
        if not notes:
            return 0.0
        return max(note.start + note.duration for note in notes)


@dataclass(frozen=True)
class SongStats:
    total_notes: int
    playable_notes: int
    unplayable_notes: int
    playable_pitches: tuple[int, ...]
    unplayable_pitches: tuple[int, ...]

    @property
    def playable_ratio(self) -> float:
        if self.total_notes == 0:
            return 1.0
        return self.playable_notes / self.total_notes


@dataclass
class PlaybackOptions:
    layout_mode: LayoutMode = LayoutMode.KEYS_36
    transpose: int = 0
    smart_remap: bool = True
    bounds: tuple[float, float, float, float] | None = None
    play_mode: PlayMode = PlayMode.SEQUENTIAL
    track_indices: tuple[int, ...] | None = None
    playlist_song_ids: tuple[str, ...] | None = None
    speed: float = 1.0
    start_offset: float = 0.0
    note_gap: float = 0.006
    on_status: Callable[[str], None] | None = None
    on_progress: Callable[[float, float], None] | None = None
    on_song_changed: Callable[[str], None] | None = None
    instrument: Instrument = Instrument.PIANO


@dataclass
class LibrarySnapshot:
    loading: bool = False
    songs: list[SongInfo] = field(default_factory=list)
    error: str | None = None

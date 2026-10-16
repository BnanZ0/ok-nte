"""Runtime support for the MIDI instrument player tab."""

from importlib import import_module
from typing import TYPE_CHECKING

__all__ = [
    "DEFAULT_21_KEY_BOUNDS",
    "DEFAULT_36_KEY_BOUNDS",
    "Instrument",
    "LayoutMode",
    "MidiAnalyzer",
    "MidiLibraryService",
    "MidiNoteEvent",
    "MidiTrackInfo",
    "MidiPlaybackController",
    "MappedPitchCache",
    "ParsedSong",
    "ParsedSongCache",
    "PianoLayout",
    "PlayMode",
    "PlaybackOptions",
    "PreparedMidiAnalysis",
    "PreparedMidiPlayback",
    "SongInfo",
    "SongStats",
]

_EXPORT_MODULES = {
    "DEFAULT_21_KEY_BOUNDS": ".models",
    "DEFAULT_36_KEY_BOUNDS": ".models",
    "Instrument": ".models",
    "LayoutMode": ".models",
    "MidiAnalyzer": ".analyzer",
    "MidiLibraryService": ".library",
    "MidiNoteEvent": ".models",
    "MidiTrackInfo": ".models",
    "MidiPlaybackController": ".controller",
    "MappedPitchCache": ".cache",
    "ParsedSong": ".models",
    "ParsedSongCache": ".cache",
    "PianoLayout": ".layout",
    "PlayMode": ".models",
    "PlaybackOptions": ".models",
    "PreparedMidiAnalysis": ".preparation",
    "PreparedMidiPlayback": ".preparation",
    "SongInfo": ".models",
    "SongStats": ".models",
}

if TYPE_CHECKING:
    from .analyzer import MidiAnalyzer
    from .cache import MappedPitchCache, ParsedSongCache
    from .controller import MidiPlaybackController
    from .layout import PianoLayout
    from .library import MidiLibraryService
    from .models import (
        DEFAULT_21_KEY_BOUNDS,
        DEFAULT_36_KEY_BOUNDS,
        Instrument,
        LayoutMode,
        MidiNoteEvent,
        MidiTrackInfo,
        ParsedSong,
        PlaybackOptions,
        PlayMode,
        SongInfo,
        SongStats,
    )
    from .preparation import PreparedMidiAnalysis, PreparedMidiPlayback


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value

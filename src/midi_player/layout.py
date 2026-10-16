from __future__ import annotations

from dataclasses import dataclass

from .models import DEFAULT_21_KEY_BOUNDS, DEFAULT_36_KEY_BOUNDS, Instrument, LayoutMode

NATURAL_OFFSETS = (0, 2, 4, 5, 7, 9, 11)
CHROMATIC_OFFSETS = tuple(range(12))
GUITAR_KEY_ROWS = (
    tuple("1234567890-="),
    tuple("qwertyuiop[]"),
    (*"asdfghjkl;'", "enter"),
)


@dataclass(frozen=True)
class PianoKey:
    pitch: int
    row: int
    column: int
    ratio_x: float
    ratio_y: float


class PianoLayout:
    """Maps MIDI pitches to normalized key-center coordinates."""

    def __init__(
        self,
        mode: LayoutMode,
        bounds: tuple[float, float, float, float],
        *,
        middle_octave_start: int | None = None,
        instrument: Instrument | str = Instrument.PIANO,
    ) -> None:
        self.mode = LayoutMode(mode)
        self.bounds = bounds
        self.instrument = Instrument(instrument)
        if middle_octave_start is None:
            middle_octave_start = 64 if self.instrument == Instrument.GUITAR else 52
        self.middle_octave_start = middle_octave_start
        self.columns = 12 if self.mode == LayoutMode.KEYS_36 else 7
        self.rows = 3
        self._keys = self._build_keys()

    @classmethod
    def from_bounds(
        cls,
        mode: LayoutMode | str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> "PianoLayout":
        return cls(LayoutMode(mode), (x1, y1, x2, y2))

    @classmethod
    def default(
        cls, mode: LayoutMode | str, *, instrument: Instrument | str = Instrument.PIANO
    ) -> "PianoLayout":
        layout_mode = LayoutMode(mode)
        bounds = DEFAULT_36_KEY_BOUNDS
        if layout_mode == LayoutMode.KEYS_21:
            bounds = DEFAULT_21_KEY_BOUNDS
        return cls(layout_mode, bounds, instrument=instrument)

    @property
    def playable_pitches(self) -> frozenset[int]:
        return frozenset(self._keys)

    @property
    def keys(self) -> tuple[PianoKey, ...]:
        return tuple(sorted(self._keys.values(), key=lambda key: (key.row, key.column)))

    def coordinate_for_pitch(self, pitch: int) -> tuple[float, float] | None:
        key = self._keys.get(pitch)
        if key is None:
            return None
        return key.ratio_x, key.ratio_y

    def keyboard_key_for_pitch(self, pitch: int) -> str | None:
        key = self._keys.get(pitch)
        if self.instrument != Instrument.GUITAR or key is None:
            return None
        return GUITAR_KEY_ROWS[key.row][key.column]

    def client_coordinate_for_pitch(
        self,
        pitch: int,
        client_width: int | float,
        client_height: int | float,
    ) -> tuple[int, int] | None:
        coord = self.coordinate_for_pitch(pitch)
        if coord is None:
            return None
        ratio_x, ratio_y = coord
        return round(ratio_x * client_width), round(ratio_y * client_height)

    def _build_keys(self) -> dict[int, PianoKey]:
        x1, y1, x2, y2 = self.bounds
        dx = (x2 - x1) / (self.columns - 1) if self.columns > 1 else 0.0
        dy = (y2 - y1) / (self.rows - 1) if self.rows > 1 else 0.0
        offsets = CHROMATIC_OFFSETS if self.mode == LayoutMode.KEYS_36 else NATURAL_OFFSETS

        keys: dict[int, PianoKey] = {}
        for row in range(self.rows):
            octave_delta = 1 - row
            octave_base = self.middle_octave_start + octave_delta * 12
            for column, semitone in enumerate(offsets):
                pitch = octave_base + semitone
                keys[pitch] = PianoKey(
                    pitch=pitch,
                    row=row,
                    column=column,
                    ratio_x=x1 + dx * column,
                    ratio_y=y1 + dy * row,
                )
        return keys

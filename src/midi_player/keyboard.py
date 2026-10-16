"""Timed guitar key events and independent held-key lifetimes."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from .layout import PianoLayout
from .models import MidiNoteEvent

logger = logging.getLogger(__name__)
KEY_RELEASE_ATTEMPTS = 2


@dataclass(frozen=True)
class KeyEvent:
    time: float
    key: str
    pressed: bool


def build_key_events(
    notes: Sequence[MidiNoteEvent],
    pitches: Sequence[int],
    layout: PianoLayout,
    *,
    speed: float,
    start_offset: float,
    minimum_duration: float,
) -> tuple[KeyEvent, ...]:
    # Simultaneous copies of one mapped pitch share one attack and the longest duration.
    intervals: dict[tuple[str, float], float] = {}
    for note, pitch in zip(notes, pitches):
        key = layout.keyboard_key_for_pitch(pitch)
        if key is None:
            continue
        start = note.start / speed
        end = start + max(note.duration / speed, minimum_duration)
        if end <= start_offset:
            continue
        start = max(start, start_offset)
        identity = (key, start)
        intervals[identity] = max(end, intervals.get(identity, end))

    events = []
    for (key, start), end in intervals.items():
        events.append(KeyEvent(start, key, True))
        events.append(KeyEvent(end, key, False))
    # Release ending notes before starting another note on the same key.
    return tuple(sorted(events, key=lambda event: (event.time, event.pressed, event.key)))


class KeyboardPlayer:
    def __init__(self, interaction) -> None:
        self.interaction = interaction
        self.active_counts: dict[str, int] = {}
        self.held_keys: set[str] = set()

    def _send_key(self, key: str, *, pressed: bool) -> None:
        sender = self.interaction.send_key_down if pressed else self.interaction.send_key_up
        if sender(key) is False:
            action = "press" if pressed else "release"
            raise OSError(f"Failed to {action} MIDI key {key}")

    def apply(self, event: KeyEvent) -> None:
        key = event.key
        if event.pressed:
            if key in self.held_keys:
                self._send_key(key, pressed=False)
                self.held_keys.remove(key)
            # An attempted press may need cleanup even when its sender reports failure.
            self.held_keys.add(key)
            self._send_key(key, pressed=True)
            self.active_counts[key] = self.active_counts.get(key, 0) + 1
        else:
            remaining = self.active_counts[key] - 1
            if remaining:
                self.active_counts[key] = remaining
            else:
                self._send_key(key, pressed=False)
                self.held_keys.remove(key)
                del self.active_counts[key]

    def release_all(self) -> None:
        error = None
        for attempt in range(KEY_RELEASE_ATTEMPTS):
            error = None
            for key in sorted(self.held_keys):
                try:
                    self._send_key(key, pressed=False)
                    self.held_keys.remove(key)
                except Exception as exc:
                    if attempt == KEY_RELEASE_ATTEMPTS - 1:
                        logger.exception("Failed to release MIDI key %s", key)
                    if error is None:
                        error = exc
            if not self.held_keys:
                return
        if error is not None:
            raise error

    def restore(self) -> None:
        for key in sorted(self.active_counts):
            self.held_keys.add(key)
            self._send_key(key, pressed=True)

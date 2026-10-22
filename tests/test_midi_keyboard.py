import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.midi_player.controller import MidiPlaybackController
from src.midi_player.keyboard import KeyboardPlayer, KeyEvent, build_key_events
from src.midi_player.layout import PianoLayout
from src.midi_player.models import (
    Instrument,
    LayoutMode,
    MidiNoteEvent,
    ParsedSong,
    PlaybackOptions,
    SongInfo,
)
from src.midi_player.preparation import PreparedMidiPlayback


def guitar_layout():
    return PianoLayout.default(LayoutMode.KEYS_36, instrument=Instrument.GUITAR)


class TestGuitarKeyEvents(unittest.TestCase):
    def test_all_36_pitches_match_the_displayed_keyboard(self):
        layout = guitar_layout()
        expected = (*"asdfghjkl;'", "enter", *"qwertyuiop[]", *"1234567890-=")
        self.assertEqual(tuple(layout.keyboard_key_for_pitch(p) for p in range(52, 88)), expected)
        self.assertIsNone(layout.keyboard_key_for_pitch(51))
        self.assertIsNone(layout.keyboard_key_for_pitch(88))
        self.assertIsNone(PianoLayout.default(LayoutMode.KEYS_36).keyboard_key_for_pitch(64))

    def test_speed_and_seek_preserve_the_remaining_duration_of_sustained_notes(self):
        notes = (
            MidiNoteEvent(52, 0, 4),
            MidiNoteEvent(64, 1, 1),
            MidiNoteEvent(76, 3, 1),
            MidiNoteEvent(55, 0, 0.5),
            MidiNoteEvent(99, 3, 1),
        )
        events = build_key_events(
            notes,
            tuple(note.pitch for note in notes),
            guitar_layout(),
            speed=2,
            start_offset=0.75,
            minimum_duration=0.006,
        )
        self.assertCountEqual(
            events,
            (
                KeyEvent(0.75, "a", True),
                KeyEvent(0.75, "q", True),
                KeyEvent(1, "q", False),
                KeyEvent(1.5, "1", True),
                KeyEvent(2, "1", False),
                KeyEvent(2, "a", False),
            ),
        )
        self.assertEqual([event.time for event in events], sorted(event.time for event in events))

    def test_duplicate_chord_notes_share_the_longest_duration(self):
        events = build_key_events(
            (MidiNoteEvent(64, 0, 0.5), MidiNoteEvent(64, 0, 1)),
            (64, 64),
            guitar_layout(),
            speed=1,
            start_offset=0,
            minimum_duration=0.006,
        )
        self.assertEqual(events, (KeyEvent(0, "q", True), KeyEvent(1, "q", False)))

    def test_zero_duration_note_has_a_bounded_press(self):
        events = build_key_events(
            (MidiNoteEvent(64, 0, 0),),
            (64,),
            guitar_layout(),
            speed=1,
            start_offset=0,
            minimum_duration=0.006,
        )
        self.assertEqual(events, (KeyEvent(0, "q", True), KeyEvent(0.006, "q", False)))

    def test_cleanup_attempts_other_keys_after_a_release_failure(self):
        interaction = RecordingInteraction(fail_up="q", fail_up_count=2)
        player = KeyboardPlayer(interaction)
        player.apply(KeyEvent(0, "q", True))
        player.apply(KeyEvent(0, "r", True))
        with patch("src.midi_player.keyboard.logger.exception"):
            with self.assertRaises(OSError):
                player.release_all()
        self.assertEqual(interaction.held, {"q"})
        player.release_all()
        self.assertEqual(interaction.held, set())


class TestGuitarPlayback(unittest.IsolatedAsyncioTestCase):
    async def test_event_preparation_does_not_consume_note_duration(self):
        clock = [0.0]
        real_sleep = asyncio.sleep

        class ClockInteraction(RecordingInteraction):
            def send_key_down(self, key):
                self.time = clock[0]
                return super().send_key_down(key)

            def send_key_up(self, key):
                self.time = clock[0]
                return super().send_key_up(key)

        def prepare_events(*args, **kwargs):
            clock[0] += 0.12
            return build_key_events(*args, **kwargs)

        async def advance_time(delay):
            clock[0] += delay
            await real_sleep(0)

        async def idle_progress(*args):
            await asyncio.Event().wait()

        interaction = ClockInteraction()
        controller = TestPlaybackController(
            prepared_song((MidiNoteEvent(64, 0, 0.04), MidiNoteEvent(67, 0.05, 0.04))),
            interaction,
        )
        with (
            patch("src.midi_player.controller.build_key_events", side_effect=prepare_events),
            patch(
                "src.utils.clock._read_monotonic",
                side_effect=lambda: clock[0],
            ),
            patch("src.midi_player.controller.asyncio.sleep", side_effect=advance_time),
            patch.object(controller, "_progress_loop", idle_progress),
        ):
            await controller.play("song", PlaybackOptions(instrument=Instrument.GUITAR))
        traces = key_action_traces(interaction.actions)
        self.assertAlmostEqual(traces["q"][1][0] - traces["q"][0][0], 0.04)
        self.assertAlmostEqual(traces["r"][1][0] - traces["r"][0][0], 0.04)
        self.assertAlmostEqual(traces["r"][0][0] - traces["q"][0][0], 0.05)

    async def test_chord_durations_and_overlapping_repeated_notes(self):
        notes = (
            MidiNoteEvent(64, 0, 0.06),
            MidiNoteEvent(67, 0, 0.1),
            MidiNoteEvent(64, 0.02, 0.06),
        )
        interaction = RecordingInteraction()
        controller = VirtualTimeController(prepared_song(notes), interaction)
        await controller.play("song", PlaybackOptions(instrument=Instrument.GUITAR))
        self.assertEqual(
            key_action_traces(interaction.actions),
            {
                "q": [(0, "down"), (0.02, "up"), (0.02, "down"), (0.08, "up")],
                "r": [(0, "down"), (0.1, "up")],
            },
        )
        self.assertEqual(interaction.held, set())

    async def test_adjacent_notes_on_one_key_release_before_retrigger(self):
        notes = (MidiNoteEvent(64, 0, 0.5), MidiNoteEvent(64, 0.5, 0.5))
        interaction = RecordingInteraction()
        controller = VirtualTimeController(prepared_song(notes), interaction)
        await controller.play("song", PlaybackOptions(instrument=Instrument.GUITAR))
        self.assertEqual(
            interaction.actions,
            [
                (0, "down", "q"),
                (0.5, "up", "q"),
                (0.5, "down", "q"),
                (1, "up", "q"),
            ],
        )

    async def test_stop_releases_all_sustained_notes(self):
        interaction = RecordingInteraction()
        controller = TestPlaybackController(
            prepared_song((MidiNoteEvent(64, 0, 60), MidiNoteEvent(67, 0, 60))), interaction
        )
        task = controller.play_background("song", PlaybackOptions(instrument=Instrument.GUITAR))
        try:
            await interaction.wait_until(lambda: interaction.held == {"q", "r"})
            await controller.stop()
            await task
            self.assertEqual(interaction.held, set())
            self.assertEqual(
                key_action_traces(interaction.actions),
                {"q": [(0, "down"), (0, "up")], "r": [(0, "down"), (0, "up")]},
            )
        finally:
            await controller.stop()
            await task

    async def test_pause_releases_and_resume_restores_active_notes(self):
        interaction = RecordingInteraction()
        controller = TestPlaybackController(prepared_song((MidiNoteEvent(64, 0, 60),)), interaction)
        task = controller.play_background("song", PlaybackOptions(instrument=Instrument.GUITAR))
        try:
            await interaction.wait_until(lambda: interaction.held == {"q"})
            controller.pause()
            await interaction.wait_until(lambda: not interaction.held)
            controller.resume()
            await interaction.wait_until(lambda: interaction.held == {"q"})
            await controller.stop()
            await task
            self.assertEqual(
                [a[1:] for a in interaction.actions],
                [
                    ("down", "q"),
                    ("up", "q"),
                    ("down", "q"),
                    ("up", "q"),
                ],
            )
        finally:
            await controller.stop()
            await task

    async def test_stop_while_paused_does_not_press_keys_again(self):
        interaction = RecordingInteraction()
        controller = TestPlaybackController(prepared_song((MidiNoteEvent(64, 0, 60),)), interaction)
        task = controller.play_background("song", PlaybackOptions(instrument=Instrument.GUITAR))
        try:
            await interaction.wait_until(lambda: interaction.held == {"q"})
            controller.pause()
            await interaction.wait_until(lambda: not interaction.held)
            await controller.stop()
            await task
            self.assertEqual([a[1:] for a in interaction.actions], [("down", "q"), ("up", "q")])
        finally:
            await controller.stop()
            await task

    async def test_input_failure_releases_keys_and_reports_playback_error(self):
        interaction = RecordingInteraction(fail_down="r")
        controller = VirtualTimeController(
            prepared_song((MidiNoteEvent(64, 0, 1), MidiNoteEvent(67, 0, 1))), interaction
        )
        statuses = []
        await controller.play(
            "song",
            PlaybackOptions(
                instrument=Instrument.GUITAR,
                on_status=statuses.append,
            ),
        )
        self.assertEqual(interaction.held, set())
        self.assertTrue(any(status.startswith("error:") for status in statuses))

    async def test_piano_keeps_using_mouse_clicks(self):
        interaction = RecordingInteraction()
        controller = VirtualTimeController(
            prepared_song((MidiNoteEvent(64, 0, 0.5),), instrument=Instrument.PIANO), interaction
        )
        await controller.play("song", PlaybackOptions())
        self.assertEqual([a[1] for a in interaction.actions], ["mouse_down", "mouse_up"])


class RecordingInteraction:
    def __init__(self, *, fail_down=None, fail_up=None, fail_up_count=1):
        self.time = 0
        self.actions = []
        self.held = set()
        self.changed = asyncio.Event()
        self.fail_down = fail_down
        self.fail_up = fail_up
        self.fail_up_count = fail_up_count

    def send_key_down(self, key):
        self.held.add(key)
        self.actions.append((self.time, "down", key))
        self.changed.set()
        if key == self.fail_down:
            return False
        return True

    def send_key_up(self, key):
        if key == self.fail_up and self.fail_up_count > 0:
            self.fail_up_count -= 1
            return False
        self.held.remove(key)
        self.actions.append((self.time, "up", key))
        self.changed.set()
        return True

    def mouse_down(self, x, y):
        self.actions.append((self.time, "mouse_down", (x, y)))

    def mouse_up(self):
        self.actions.append((self.time, "mouse_up", None))

    async def wait_until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                self.changed.clear()
                await self.changed.wait()


class TestPlaybackController(MidiPlaybackController):
    def __init__(self, prepared, interaction):
        og = SimpleNamespace(
            executor=SimpleNamespace(
                method=SimpleNamespace(width=1920, height=1080),
                interaction=interaction,
            )
        )
        super().__init__(SimpleNamespace(list_songs=lambda: []), og_provider=og)
        self.prepared = prepared

    @staticmethod
    def _prepare_capture(og):
        pass

    async def _prepare_playback(self, song_id, options):
        return self.prepared


class VirtualTimeController(TestPlaybackController):
    async def _wait_until_playback_time(self, playback_origin, playback_time, *, keyboard=None):
        self.og_provider.executor.interaction.time = playback_time
        await asyncio.sleep(0)
        return playback_origin


def key_action_traces(actions):
    traces = {}
    for time, action, key in actions:
        traces.setdefault(key, []).append((time, action))
    return traces


def prepared_song(notes, *, instrument=Instrument.GUITAR):
    info = SongInfo("song", "Song", Path("song.mid"), 1, 1)
    parsed = ParsedSong(info, max(note.start + note.duration for note in notes), 480, notes)
    return PreparedMidiPlayback(
        "song",
        parsed,
        notes,
        tuple(note.pitch for note in notes),
        parsed.duration,
        PianoLayout.default(LayoutMode.KEYS_36, instrument=instrument),
    )


if __name__ == "__main__":
    unittest.main()

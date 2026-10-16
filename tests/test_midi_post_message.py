import asyncio
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.midi_player.keyboard import KeyboardPlayer, KeyEvent
from src.midi_player.models import Instrument, MidiNoteEvent, PlaybackOptions
from tests.test_midi_keyboard import VirtualTimeController, prepared_song


@unittest.skipUnless(os.name == "nt", "Windows PostMessage adapter")
class TestMidiPostMessage(unittest.TestCase):
    def setUp(self):
        import win32con

        from src.interaction.NTEInteraction import NTEInteraction

        self.down = win32con.WM_KEYDOWN
        self.up = win32con.WM_KEYUP
        self.held = set()
        self.failures = {}
        self.interaction = NTEInteraction.__new__(NTEInteraction)
        self.interaction._input_lock = threading.RLock()
        self.interaction._disable_key_mapping = 1
        self.interaction._dynamic_target_hwnd = 0
        self.interaction.hwnd_window = SimpleNamespace(hwnd=123, top_hwnd=0)
        self.interaction.try_activate = lambda: None
        messages = patch(
            "src.interaction.NTEInteraction.win32gui.PostMessage", side_effect=self.post_message
        )
        messages.start()
        self.addCleanup(messages.stop)
        logging = patch("src.interaction.NTEInteraction.logger.error")
        logging.start()
        self.addCleanup(logging.stop)

    def post_message(self, hwnd, message, key, lparam):
        failure = (message, key)
        if self.failures.get(failure, 0):
            self.failures[failure] -= 1
            raise OSError("Simulated PostMessage failure")
        if message == self.down:
            self.held.add(key)
        elif message == self.up:
            self.held.discard(key)

    def test_failed_note_release_propagates_and_cleanup_retries_it(self):
        player = KeyboardPlayer(self.interaction)
        player.apply(KeyEvent(0, "q", True))
        player.apply(KeyEvent(0, "r", True))
        self.failures[(self.up, ord("Q"))] = 1
        with self.assertRaises(OSError):
            player.apply(KeyEvent(1, "q", False))
        player.release_all()
        self.assertEqual(self.held, set())
        self.assertEqual(player.held_keys, set())

    def test_key_methods_return_success_and_failure_from_post(self):
        self.assertIs(self.interaction.send_key_down("q"), True)
        self.failures[(self.up, ord("Q"))] = 1
        self.assertIs(self.interaction.send_key_up("q"), False)
        self.assertEqual(self.held, {ord("Q")})
        self.assertIs(self.interaction.send_key_up("q"), True)
        self.assertEqual(self.held, set())
        self.failures[(self.down, ord("R"))] = 1
        self.assertIs(self.interaction.send_key_down("r"), False)
        self.assertEqual(self.held, set())

    def test_stop_cleanup_retries_one_transient_release_failure(self):
        player = KeyboardPlayer(self.interaction)
        player.apply(KeyEvent(0, "q", True))
        player.apply(KeyEvent(0, "r", True))
        self.failures[(self.up, ord("Q"))] = 1
        player.release_all()
        self.assertEqual(self.held, set())
        self.assertEqual(player.held_keys, set())

    def test_failed_key_down_cleans_up_and_reports_playback_error(self):
        self.failures[(self.down, ord("R"))] = 1
        controller = VirtualTimeController(
            prepared_song((MidiNoteEvent(64, 0, 1), MidiNoteEvent(67, 0, 1))), self.interaction
        )
        statuses = []
        asyncio.run(
            controller.play(
                "song", PlaybackOptions(instrument=Instrument.GUITAR, on_status=statuses.append)
            )
        )
        self.assertEqual(self.held, set())
        self.assertTrue(any(status.startswith("error:") for status in statuses))

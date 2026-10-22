import unittest
from unittest.mock import Mock, patch

from ok import og

from src.config import cursor_sync_config_option
from src.interaction.cursor_sync import (
    _CURSOR_SYNC_CONFIG_NAME,
    _ENABLE_CURSOR_SYNC_KEY,
    CursorSync,
)


class CursorSyncTest(unittest.TestCase):
    def _make_cursor_sync(self):
        cursor_sync = CursorSync()
        cursor_sync._has_game_window = lambda: True
        cursor_sync._is_game_foreground = lambda: False
        cursor_sync._get_geometry = lambda: ((100, 100), (10, 10))
        return cursor_sync

    def test_cursor_sync_is_enabled_by_default(self):
        self.assertTrue(cursor_sync_config_option.default_config[_ENABLE_CURSOR_SYNC_KEY])

    def test_worker_restores_an_external_jump_to_center(self):
        cursor_sync = self._make_cursor_sync()

        wait_count = 0

        def wait():
            nonlocal wait_count
            wait_count += 1
            if wait_count == 2:
                cursor_sync._stop_event.set()

        cursor_sync._wait = wait
        with (
            patch(
                "src.interaction.cursor_sync.GetCursorPos",
                side_effect=[(20, 20), (100, 100)],
            ),
            patch("src.interaction.cursor_sync.SetCursorPos") as set_cursor_pos,
        ):
            cursor_sync._worker()

        set_cursor_pos.assert_called_once_with((20, 20))

    def test_internal_center_move_updates_sampling_state_without_resetting_baseline(self):
        cursor_sync = self._make_cursor_sync()
        cursor_sync._center = (100, 100)
        cursor_sync._limit = (10, 10)
        cursor_sync._last_cursor_position = (20, 20)
        cursor_sync._last_sample_was_outside = True

        with patch("src.interaction.cursor_sync.SetCursorPos") as set_cursor_pos:
            cursor_sync.set_cursor_pos((100, 100))

        self.assertEqual(cursor_sync._last_cursor_position, (20, 20))
        self.assertFalse(cursor_sync._last_sample_was_outside)
        self.assertTrue(cursor_sync._ignore_until)
        set_cursor_pos.assert_called_once_with((100, 100))

    def test_geometry_uses_global_window_and_executor_method(self):
        cursor_sync = CursorSync()
        hwnd_window = type(
            "Window",
            (),
            {
                "hwnd": 1,
                "top_hwnd": 2,
                "get_top_window_cords": lambda self, x, y: (x + 10, y + 20),
            },
        )()
        device_manager = type("DeviceManager", (), {"hwnd_window": hwnd_window})()
        method = type("Method", (), {"width": 2000, "height": 1000})()
        executor = type("Executor", (), {"method": method})()

        with (
            patch.object(og, "device_manager", device_manager, create=True),
            patch.object(og, "executor", executor, create=True),
            patch("src.interaction.cursor_sync.win32gui.ClientToScreen", return_value=(1010, 520)),
        ):
            center, limit = cursor_sync._get_geometry()

        self.assertEqual(center, (1010, 520))
        self.assertEqual(limit, (30.0, 15.0))

    def test_missing_game_window_skips_geometry(self):
        cursor_sync = CursorSync()
        hwnd_window = type("Window", (), {"hwnd": 0})()
        device_manager = type("DeviceManager", (), {"hwnd_window": hwnd_window})()

        with patch.object(og, "device_manager", device_manager, create=True):
            self.assertFalse(cursor_sync._has_game_window())
            self.assertIsNone(cursor_sync._get_geometry())

    def test_cursor_sync_can_be_disabled_from_global_config(self):
        cursor_sync = CursorSync()
        disabled_config = {_ENABLE_CURSOR_SYNC_KEY: False}
        get_config = Mock(return_value=disabled_config)
        global_config = type("GlobalConfig", (), {"get_config": get_config})()

        with patch.object(og, "global_config", global_config, create=True):
            self.assertFalse(cursor_sync._is_enabled())
        get_config.assert_called_once_with(_CURSOR_SYNC_CONFIG_NAME)

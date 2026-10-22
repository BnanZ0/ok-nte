import unittest
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src import LAUNCHER_EXE
from src.tasks.LauncherTask import LauncherTask


class TestLauncherTask(unittest.TestCase):
    def test_update_wait_extends_timeout_by_elapsed_seconds(self):
        task = object.__new__(LauncherTask)
        with patch("src.utils.clock._read_monotonic", return_value=100) as read_clock:
            started = task.clock.now()
            timer = task.clock.timer(timeout=10)
            read_clock.return_value = 103
            task._extend_timeout_for_update(timer, started)
            read_clock.return_value = 110
            self.assertFalse(timer.expired())
            read_clock.return_value = 113
            self.assertTrue(timer.expired())

    def _make_task(self):
        task = object.__new__(LauncherTask)
        task.log_info = Mock()
        task.log_warning = Mock()
        task.log_info_gated = Mock()
        task.sleep = Mock()
        task.capture_config = Mock()
        task.capture_config.GAME_CAPTURE_CONFIG = {"windows": {"hwnd_class": "UnrealWindow"}}
        task.capture_config.LAUNCHER_CAPTURE_CONFIG = {
            "windows": {"hwnd_class": "Qt51517QWindowOwnDC"}
        }
        return task

    def test_hidden_launcher_is_shown_before_capture(self):
        task = self._make_task()

        with (
            patch("src.tasks.LauncherTask.win32gui.IsIconic", side_effect=[False, False]),
            patch("src.tasks.LauncherTask.win32gui.IsWindowVisible", side_effect=[False, True]),
            patch("src.tasks.LauncherTask.win32gui.ShowWindow") as show_window,
        ):
            self.assertTrue(task._restore_window_if_minimized(123, "NTEGame.exe"))

        show_window.assert_called_once_with(123, 5)

    def test_capture_stops_when_launcher_cannot_be_shown(self):
        task = self._make_task()
        task.capture_config = Mock()
        task._ensure_launcher_visible = Mock(return_value=False)

        with self.assertRaisesRegex(TaskDisabledException, "Launcher window is not visible"):
            task._capture_launcher()

    def test_find_process_window_uses_launcher_capture_window_class(self):
        task = self._make_task()
        proc = {"pid": 1, "name": LAUNCHER_EXE[0]}
        task._find_window_for_process = Mock(return_value=123)

        with patch(
            "src.tasks.LauncherTask.psutil.process_iter",
            return_value=[Mock(info=proc)],
        ):
            self.assertEqual((proc, 123), task._find_process_window(LAUNCHER_EXE))

        task._find_window_for_process.assert_called_once_with(
            proc,
            hwnd_class="Qt51517QWindowOwnDC",
            require_title=False,
        )

    def test_find_window_callback_continues_after_visible_match(self):
        task = self._make_task()

        def enum_windows(callback, _):
            self.assertTrue(callback(101, None))
            self.assertTrue(callback(102, None))

        with (
            patch("src.tasks.LauncherTask.win32gui.EnumWindows", side_effect=enum_windows),
            patch("src.tasks.LauncherTask.win32gui.IsWindow", return_value=True),
            patch("src.tasks.LauncherTask.win32gui.IsWindowEnabled", return_value=True),
            patch(
                "src.tasks.LauncherTask.win32process.GetWindowThreadProcessId",
                return_value=(0, 1),
            ),
            patch("src.tasks.LauncherTask.win32gui.IsWindowVisible", side_effect=[True, False]),
        ):
            self.assertEqual(task._find_window_for_process({"pid": 1}), 101)

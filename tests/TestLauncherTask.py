import unittest
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src import GAME_EXE, LAUNCHER_EXE
from src.patches.cloud_launch_guard import CLOUD_EXE, CLOUD_WINDOW_CLASS_PATTERN
from src.tasks.LauncherTask import LauncherTask, _matches_window_class


class TestLauncherTask(unittest.TestCase):
    def test_launcher_display_name_follows_window_variant(self):
        with patch(
            "src.tasks.LauncherTask.is_cloud_window_selected", return_value=True
        ):
            self.assertEqual(
                LauncherTask._launcher_display_name(), "准备云游戏窗口"
            )
        with patch(
            "src.tasks.LauncherTask.is_cloud_window_selected", return_value=False
        ):
            self.assertEqual(LauncherTask._launcher_display_name(), "Start Game")

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

    def test_find_process_window_uses_the_explicit_window_class(self):
        task = self._make_task()
        proc = {"pid": 1, "name": LAUNCHER_EXE[0]}
        task._find_window_for_process = Mock(return_value=123)

        with patch(
            "src.tasks.LauncherTask.psutil.process_iter",
            return_value=[Mock(info=proc)],
        ):
            self.assertEqual(
                (proc, 123),
                task._find_process_window(
                    LAUNCHER_EXE, hwnd_class="Qt51517QWindowOwnDC"
                ),
            )

        task._find_window_for_process.assert_called_once_with(
            proc,
            hwnd_class="Qt51517QWindowOwnDC",
            require_title=False,
        )

    def test_window_class_for_selects_local_game_and_launcher(self):
        task = self._make_task()

        self.assertEqual(task._window_class_for(GAME_EXE), "UnrealWindow")
        self.assertEqual(
            task._window_class_for(LAUNCHER_EXE), "Qt51517QWindowOwnDC"
        )
        self.assertIsNone(task._window_class_for("NTECloudGame.exe"))

    def test_window_class_matcher_supports_exact_strings_and_regex(self):
        self.assertTrue(_matches_window_class("UnrealWindow", "UnrealWindow"))
        self.assertFalse(_matches_window_class("UnrealWindow", "Qt51517QWindowIcon"))
        self.assertTrue(
            _matches_window_class("Qt51517QWindowOwnDC", CLOUD_WINDOW_CLASS_PATTERN)
        )
        self.assertTrue(
            _matches_window_class("Qt680QWindowIcon", [CLOUD_WINDOW_CLASS_PATTERN])
        )
        self.assertFalse(
            _matches_window_class(
                "Qt51517QWindowToolSaveBitsOwnDC", CLOUD_WINDOW_CLASS_PATTERN
            )
        )

    def test_wait_for_window_restores_and_returns_the_window(self):
        task = self._make_task()
        proc = {"pid": 1, "name": "HTGame.exe"}
        task._find_process_window = Mock(return_value=(proc, 123))
        task._restore_window_if_minimized = Mock(return_value=True)
        task._get_window_size = Mock(return_value=(1920, 1080))
        task._sleep_for_window_poll = Mock()

        with patch("src.utils.clock._read_monotonic", return_value=100):
            result = task._wait_for_window(
                "HTGame.exe",
                hwnd_class="UnrealWindow",
                settle_window=False,
                time_out=5,
            )

        self.assertEqual(result, (proc, 123))
        task._restore_window_if_minimized.assert_called_once_with(123, "HTGame.exe")

    def test_wait_for_process_uses_the_local_game_window_class(self):
        task = self._make_task()
        proc = {"pid": 1, "name": "HTGame.exe"}
        task._wait_for_window = Mock(return_value=(proc, 123))

        self.assertEqual(task._wait_for_process("HTGame.exe", time_out=5), proc)
        task._wait_for_window.assert_called_once_with(
            "HTGame.exe",
            hwnd_class="UnrealWindow",
            require_title=False,
            settle_window=False,
            time_out=5,
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

    def _make_cloud_task(self):
        """Cloud placeholder task with a virtual clock (no real sleeping)."""
        task = object.__new__(LauncherTask)
        task.log_info = Mock()
        task.log_warning = Mock()
        task.log_error = Mock()
        task.log_debug_gated = Mock()
        task.scene = Mock()
        # executor is a read-only property backed by _executor.
        executor = Mock()
        executor.device_manager = Mock()
        executor.device_manager.hwnd_window = Mock()
        executor.device_manager.hwnd_window.hwnd = 6163716
        executor.device_manager.capture_method = None
        task._executor = executor
        task._activate_cloud_game_window = Mock()
        task._wait_for_window = Mock(return_value=(Mock(), 6163716))
        now = [1000.0]

        def fake_sleep(seconds):
            now[0] += seconds

        task.sleep = Mock(side_effect=fake_sleep)
        return task, now

    def test_cloud_placeholder_rebuilds_wgc_and_marks_capture_ready(self):
        task, now = self._make_cloud_task()
        task.executor.connected.side_effect = lambda: now[0] >= 1001.0

        with (
            patch("src.tasks.LauncherTask.is_cloud_window_selected", return_value=True),
            patch("src.utils.clock._read_monotonic", side_effect=lambda: now[0]),
            patch(
                "src.patches.cloud_launch_guard.should_block_cloud_launch",
                return_value=False,
            ),
        ):
            self.assertTrue(task._placeholder_cloud_auto_start_and_short_circuit())

        task.executor.device_manager.use_windows_capture.assert_called_once()
        task.scene.set_game_capture_ready.assert_called_once_with(True)
        window_args, window_kwargs = task._wait_for_window.call_args
        self.assertEqual(window_args[0], CLOUD_EXE)
        self.assertIs(window_kwargs["hwnd_class"], CLOUD_WINDOW_CLASS_PATTERN)
        self.assertFalse(window_kwargs["settle_window"])
        task._activate_cloud_game_window.assert_called_once_with(6163716)

    def test_cloud_placeholder_does_not_mark_ready_when_wgc_never_starts(self):
        task, now = self._make_cloud_task()
        task.executor.connected.return_value = False

        with (
            patch("src.tasks.LauncherTask.is_cloud_window_selected", return_value=True),
            patch("src.utils.clock._read_monotonic", side_effect=lambda: now[0]),
            patch(
                "src.patches.cloud_launch_guard.should_block_cloud_launch",
                return_value=False,
            ),
        ):
            self.assertTrue(task._placeholder_cloud_auto_start_and_short_circuit())

        task.scene.set_game_capture_ready.assert_called_once_with(False)
        task.log_error.assert_called_once()
        self.assertGreater(task.executor.device_manager.use_windows_capture.call_count, 1)

    def test_local_variant_does_not_enter_cloud_placeholder(self):
        task = self._make_cloud_task()[0]
        task.executor.connected.return_value = False

        with patch("src.tasks.LauncherTask.is_cloud_window_selected", return_value=False):
            self.assertFalse(task._placeholder_cloud_auto_start_and_short_circuit())

        task.executor.device_manager.use_windows_capture.assert_not_called()
        task.scene.set_game_capture_ready.assert_not_called()

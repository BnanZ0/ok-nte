import unittest
from unittest.mock import ANY, Mock, patch

from ok import TaskDisabledException

from src.patches import cloud_launch_guard as guard
from tests.TestNTEOneTimeTask import _OneTimeTask


class _FakeConfig(dict):
    def __init__(self):
        super().__init__({guard._DONT_SHOW_KEY: False})
        self.saved = 0

    def save_file(self):
        self.saved += 1


class TestCloudWindowCheck(unittest.TestCase):
    def test_reports_available_window(self):
        with patch.object(guard, "_find_cloud_window_hwnds", return_value=[6163716]):
            self.assertTrue(guard.cloud_window_available())

    def test_reports_missing_window(self):
        with patch.object(guard, "_find_cloud_window_hwnds", return_value=[]):
            self.assertFalse(guard.cloud_window_available())

    def test_check_failure_fails_closed(self):
        with patch("win32gui.EnumWindows", side_effect=RuntimeError("enum failed")):
            self.assertFalse(guard.cloud_window_available())

    def test_minimized_qt_window_is_detected_by_class_family_and_iconic_state(self):
        def fake_enum(callback, _):
            callback(6163716, None)

        with (
            patch("win32gui.EnumWindows", side_effect=fake_enum),
            patch("win32gui.IsWindow", return_value=True),
            patch("win32gui.IsWindowEnabled", return_value=True),
            patch("win32gui.GetClassName", return_value="Qt51517QWindowOwnDC"),
            patch(
                "ok.util.window.get_exe_by_hwnd",
                return_value=(
                    "NTECloudGame",
                    r"C:\Games\NTECloud\NTECloudGame.exe",
                    "",
                ),
            ),
            patch("win32gui.IsIconic", return_value=True),
        ):
            self.assertTrue(guard.cloud_window_available())
            self.assertTrue(guard.cloud_window_minimized())

    def test_qt_version_change_does_not_break_window_detection(self):
        def fake_enum(callback, _):
            callback(6163716, None)

        with (
            patch("win32gui.EnumWindows", side_effect=fake_enum),
            patch("win32gui.IsWindow", return_value=True),
            patch("win32gui.IsWindowEnabled", return_value=True),
            patch("win32gui.GetClassName", return_value="Qt680QWindowIcon"),
            patch(
                "ok.util.window.get_exe_by_hwnd",
                return_value=(
                    "NTECloudGame",
                    r"C:\Games\NTECloud\NTECloudGame.exe",
                    "",
                ),
            ),
        ):
            self.assertTrue(guard.cloud_window_available())

    def test_restore_cloud_windows_only_restores_iconic_windows(self):
        with (
            patch("win32gui.IsIconic", side_effect=[True, False]),
            patch("win32gui.ShowWindow") as show,
            patch.object(guard.logger, "info"),
        ):
            guard._restore_cloud_windows([1, 2])

        # win32con.SW_RESTORE == 9
        show.assert_called_once_with(1, 9)

    def test_window_state_log_includes_class_and_iconic_state(self):
        with (
            patch("win32gui.GetClassName", return_value="Qt51517QWindowOwnDC"),
            patch("win32gui.IsIconic", return_value=True),
            patch("win32gui.IsWindowVisible", return_value=True),
            patch("win32gui.GetWindowText", return_value="云·异环"),
            patch.object(guard.logger, "info") as info,
        ):
            guard._log_cloud_window_state([123])

        info.assert_called_once()
        message = info.call_args.args[0]
        self.assertIn("Qt51517QWindowOwnDC", message)
        self.assertIn("iconic=True", message)

    def test_minimized_notice_is_throttled_and_emits_info_bar(self):
        from src.events import communicate

        with (
            patch.object(guard, "_last_minimized_notice_at", None),
            patch.object(guard.clock, "now", side_effect=[1000.0, 1001.0]),
            patch.object(communicate, "info_bar_requested") as signal,
        ):
            self.assertTrue(guard.show_cloud_minimized_notice())
            self.assertFalse(guard.show_cloud_minimized_notice())

        signal.emit.assert_called_once()


class TestNoticePreference(unittest.TestCase):
    def test_dont_show_again_is_persisted(self):
        config = _FakeConfig()
        with patch.object(guard, "_config", return_value=config):
            self.assertFalse(guard.notice_dismissed())
            self.assertTrue(guard.set_notice_dismissed())
            self.assertTrue(guard.notice_dismissed())
        self.assertEqual(config[guard._DONT_SHOW_KEY], True)
        self.assertEqual(config.saved, 1)

    def test_preference_read_failure_defaults_to_show(self):
        with patch.object(guard, "_config", side_effect=RuntimeError("no config")):
            self.assertFalse(guard.notice_dismissed())


class TestShouldBlockCloudLaunch(unittest.TestCase):
    def test_local_variant_never_checks_or_dialogs(self):
        with (
            patch.object(guard, "_cloud_selected", return_value=False),
            patch.object(guard, "cloud_window_available") as check,
            patch.object(guard, "show_cloud_missing_dialog") as dialog,
        ):
            self.assertFalse(guard.should_block_cloud_launch())
        check.assert_not_called()
        dialog.assert_not_called()

    def test_cloud_with_window_is_allowed(self):
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "_find_cloud_window_hwnds", return_value=[123]),
            patch.object(guard, "_log_cloud_window_state"),
            patch("win32gui.IsIconic", return_value=False),
            patch.object(guard, "show_cloud_missing_dialog") as dialog,
            patch.object(guard, "show_cloud_minimized_notice") as notice,
        ):
            self.assertFalse(guard.should_block_cloud_launch())
        dialog.assert_not_called()
        notice.assert_not_called()

    def test_minimized_window_is_restored_and_not_blocked(self):
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "_find_cloud_window_hwnds", return_value=[123]),
            patch.object(guard, "_log_cloud_window_state"),
            patch.object(guard, "_restore_cloud_windows") as restore,
            patch("win32gui.IsIconic", return_value=True),
            patch.object(guard, "show_cloud_missing_dialog") as dialog,
            patch.object(guard, "show_cloud_minimized_notice") as notice,
        ):
            self.assertFalse(guard.should_block_cloud_launch())
        dialog.assert_not_called()
        restore.assert_called_once_with([123])
        notice.assert_called_once()

    def test_cloud_without_window_is_blocked_after_dialog(self):
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "_find_cloud_window_hwnds", return_value=[]),
            patch.object(guard, "_log_cloud_window_state"),
            patch.object(guard, "show_cloud_missing_dialog") as dialog,
        ):
            self.assertTrue(guard.should_block_cloud_launch())
        dialog.assert_called_once()

    def test_headless_still_blocks_even_without_a_dialog(self):
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "_find_cloud_window_hwnds", return_value=[]),
            patch.object(guard, "_log_cloud_window_state"),
            patch("PySide6.QtWidgets.QApplication.instance", return_value=None),
        ):
            self.assertTrue(guard.should_block_cloud_launch())


class TestStartControllerPatch(unittest.TestCase):
    def test_start_is_cancelled_when_window_missing(self):
        original = Mock(return_value="started")
        wrapped = guard._wrap_start(original)
        controller = object()

        with patch.object(guard, "should_block_cloud_launch", return_value=True):
            self.assertIsNone(wrapped(controller, "task", True))

        original.assert_not_called()

    def test_start_passes_through_when_window_present(self):
        original = Mock(return_value="started")
        wrapped = guard._wrap_start(original)

        with patch.object(guard, "should_block_cloud_launch", return_value=False):
            self.assertEqual(wrapped(object(), "task", True), "started")

        original.assert_called_once_with(ANY, "task", True)


class TestWindowSelectionNotice(unittest.TestCase):
    def _wrapped(self):
        original = Mock(return_value=None)
        return original, guard._wrap_set_preferred_device(original)

    def test_explicit_cloud_selection_shows_notice(self):
        original, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", side_effect=[False, True]),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object(), index=1)
        notice.assert_called_once()
        self.assertEqual(original.call_args.kwargs, {"imei": None, "index": 1})

    def test_device_list_refresh_on_cloud_does_not_repop_notice(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", side_effect=[True, True]),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object(), index=1)
        notice.assert_not_called()

    def test_internal_no_arg_call_does_not_show_notice(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", side_effect=[False, True]),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object())
        notice.assert_not_called()

    def test_dismissed_notice_is_not_shown_again(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", side_effect=[False, True]),
            patch.object(guard, "notice_dismissed", return_value=True),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object(), imei="pc_cloud")
        notice.assert_not_called()

    def test_local_selection_does_not_show_notice(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", return_value=False),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object(), index=0)
        notice.assert_not_called()


class TestDialogHelpers(unittest.TestCase):
    def test_usage_notice_is_skipped_without_gui(self):
        with patch.object(guard, "_gui_thread", return_value=None):
            self.assertFalse(guard.show_cloud_usage_notice())

    def test_missing_dialog_cooldown_suppresses_duplicates(self):
        with (
            patch("PySide6.QtWidgets.QApplication.instance", return_value=Mock()),
            patch.object(guard, "_last_missing_dialog_at", 0.0),
            patch("src.ui.foundation.dialogs.show_dialog_and_wait") as show,
            patch.object(guard.clock, "now", side_effect=[1000.0, 1001.0]),
        ):
            self.assertTrue(guard.show_cloud_missing_dialog())
            self.assertFalse(guard.show_cloud_missing_dialog())
        show.assert_called_once()


class TestTaskFallback(unittest.TestCase):
    def test_one_time_task_disables_itself_when_window_missing(self):
        task = _OneTimeTask(game_capture_ready=True, connected=True)
        with (
            patch("src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=True),
            patch("src.tasks.NTEOneTimeTask._cloud_launch_blocked", return_value=True),
        ):
            with self.assertRaisesRegex(
                TaskDisabledException, "Cloud game window is not open"
            ):
                task.run()
        task.disable.assert_called_once()
        task.log_error.assert_called_once()
        self.assertFalse(task.base_run_called)

    def test_one_time_task_still_runs_when_window_present(self):
        task = _OneTimeTask(game_capture_ready=True, connected=True)
        with (
            patch("src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=True),
            patch("src.tasks.NTEOneTimeTask._cloud_launch_blocked", return_value=False),
        ):
            task.run()
        self.assertTrue(task.base_run_called)
        task.disable.assert_not_called()

    def test_launcher_placeholder_blocks_when_window_missing(self):
        from src.tasks.LauncherTask import LauncherTask

        task = object.__new__(LauncherTask)
        task.log_info = Mock()
        task.log_warning = Mock()
        task.log_error = Mock()
        task.scene = Mock()
        task.disable = Mock()

        with (
            patch("src.tasks.LauncherTask.is_cloud_window_selected", return_value=True),
            patch(
                "src.patches.cloud_launch_guard.should_block_cloud_launch",
                return_value=True,
            ),
        ):
            with self.assertRaisesRegex(
                TaskDisabledException, "Cloud game window is not open"
            ):
                task._placeholder_cloud_auto_start_and_short_circuit()

        task.disable.assert_called_once()
        task.scene.set_game_capture_ready.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()

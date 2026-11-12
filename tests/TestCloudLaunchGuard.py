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

    def test_rewritten_notice_ignores_an_older_dont_show_again(self):
        config = _FakeConfig()
        config[guard._DONT_SHOW_KEY] = True
        with patch.object(guard, "_config", return_value=config):
            # No notice_version key: the flag belongs to the previous wording.
            self.assertFalse(guard.notice_dismissed())
            self.assertTrue(guard.set_notice_dismissed())
            self.assertEqual(
                config[guard._NOTICE_VERSION_KEY], guard.notice_revision()
            )
            self.assertTrue(guard.notice_dismissed())

    def test_current_notice_version_is_recorded(self):
        config = _FakeConfig()
        with patch.object(guard, "_config", return_value=config):
            guard.set_notice_dismissed()
            self.assertTrue(guard.notice_dismissed())
        self.assertEqual(config[guard._NOTICE_VERSION_KEY], guard.notice_revision())

    def test_legacy_numeric_version_no_longer_suppresses_the_notice(self):
        config = _FakeConfig()
        config[guard._DONT_SHOW_KEY] = True
        config[guard._NOTICE_VERSION_KEY] = 2
        with patch.object(guard, "_config", return_value=config):
            self.assertFalse(guard.notice_dismissed())

    def test_revision_is_derived_from_the_copy(self):
        before = guard.notice_revision()
        self.assertEqual(before, guard.notice_revision())
        with patch.object(guard, "NOTICE_CONTENT", guard.NOTICE_CONTENT + "补充一句"):
            self.assertNotEqual(guard.notice_revision(), before)

    def test_edited_copy_force_shows_a_previously_dismissed_notice(self):
        config = _FakeConfig()
        with patch.object(guard, "_config", return_value=config):
            guard.set_notice_dismissed()
            self.assertTrue(guard.notice_dismissed())
        with (
            patch.object(guard, "_config", return_value=config),
            patch.object(guard, "NOTICE_CONTENT", guard.NOTICE_CONTENT + "补充一句"),
        ):
            self.assertFalse(guard.notice_dismissed())

    def test_button_label_change_also_invalidates_dismissal(self):
        config = _FakeConfig()
        with patch.object(guard, "_config", return_value=config):
            guard.set_notice_dismissed()
        with (
            patch.object(guard, "_config", return_value=config),
            patch.object(guard, "NOTICE_ACK_LABEL", "我了解了"),
        ):
            self.assertFalse(guard.notice_dismissed())

    @staticmethod
    def _show_notice(checked, persist):
        checkbox = Mock()
        checkbox.isChecked.return_value = checked
        with (
            patch.object(guard, "_gui_thread", return_value=Mock()),
            patch.object(guard.og, "main_window", Mock(), create=True),
            patch("PySide6.QtWidgets.QLabel", Mock()),
            patch("PySide6.QtWidgets.QCheckBox", return_value=checkbox),
            patch("PySide6.QtWidgets.QScrollArea", Mock()),
            patch("PySide6.QtWidgets.QWidget", Mock()),
            patch("PySide6.QtWidgets.QVBoxLayout", Mock()),
            patch("src.ui.foundation.dialogs.MessageBoxBase"),
            patch.object(guard, "set_notice_dismissed", persist),
        ):
            return guard.show_cloud_usage_notice()

    def test_unchecked_notice_does_not_persist_a_preference(self):
        persist = Mock(return_value=True)
        self.assertTrue(self._show_notice(False, persist))
        persist.assert_not_called()

    def test_checked_notice_persists_dismissal(self):
        persist = Mock(return_value=True)
        self.assertTrue(self._show_notice(True, persist))
        persist.assert_called_once_with()

    def test_other_dialog_copy_does_not_invalidate_the_notice(self):
        before = guard.notice_revision()
        with patch.object(guard, "MISSING_CONTENT", guard.MISSING_CONTENT + "补充一句"):
            self.assertEqual(guard.notice_revision(), before)

    def test_verified_scope_lists_three_tasks_and_no_task_type_claims(self):
        self.assertIn("自动战斗、异象追猎、异象界域", guard.NOTICE_CONTENT)
        self.assertIn("其他任务均未在云版验证", guard.NOTICE_CONTENT)
        self.assertNotIn("一次性任务", guard.NOTICE_CONTENT)
        self.assertNotIn("触发式任务", guard.NOTICE_CONTENT)

    def test_stale_preference_query_does_not_log_repeatedly(self):
        config = _FakeConfig()
        config[guard._DONT_SHOW_KEY] = True
        with (
            patch.object(guard, "_config", return_value=config),
            patch.object(guard.logger, "info") as info,
        ):
            self.assertFalse(guard.notice_dismissed())
            self.assertFalse(guard.notice_dismissed())
        info.assert_not_called()

    def test_dismissal_writes_the_preference_exactly_once(self):
        config = _FakeConfig()
        with patch.object(guard, "_config", return_value=config):
            self.assertTrue(guard.set_notice_dismissed())
        self.assertEqual(config.saved, 1)
        self.assertEqual(config[guard._DONT_SHOW_KEY], True)
        self.assertEqual(config[guard._NOTICE_VERSION_KEY], guard.notice_revision())

    def test_notice_dialog_builds_title_body_and_checkbox(self):
        checkbox = Mock()
        checkbox.isChecked.return_value = False
        with (
            patch.object(guard, "_gui_thread", return_value=Mock()),
            patch.object(guard.og, "main_window", Mock(), create=True),
            patch("PySide6.QtWidgets.QLabel", Mock()),
            patch("PySide6.QtWidgets.QCheckBox", return_value=checkbox),
            patch("PySide6.QtWidgets.QScrollArea", Mock()),
            patch("PySide6.QtWidgets.QWidget", Mock()),
            patch("PySide6.QtWidgets.QVBoxLayout", Mock()),
            patch("src.ui.foundation.dialogs.MessageBoxBase") as message_box,
            patch.object(guard, "set_notice_dismissed"),
        ):
            self.assertTrue(guard.show_cloud_usage_notice())
        dialog = message_box.return_value
        dialog.widget.setMinimumWidth.assert_called_once_with(480)
        # Scroll area (title/body) + "don't show again" checkbox.
        self.assertEqual(dialog.viewLayout.addWidget.call_count, 2)
        dialog.yesButton.setText.assert_called_once()
        dialog.cancelButton.hide.assert_called_once()
        dialog.exec.assert_called_once()

    def test_install_migrates_preference_before_gui_handles_selection(self):
        with (
            patch("ok.core.start_controller.StartController"),
            patch("ok.device.DeviceManager.DeviceManager"),
            patch.object(guard, "_PATCH_INSTALLED", False),
            patch.object(guard, "_config") as config,
        ):
            guard.install_cloud_launch_guard()
        config.assert_called_once_with()


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
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "notice_revision", return_value="rev"),
            patch.object(guard, "_shown_notice_revision", None),
            patch.object(guard, "show_cloud_usage_notice", return_value=True) as notice,
        ):
            wrapped(object(), index=1)
        notice.assert_called_once()
        self.assertEqual(original.call_args.kwargs, {"imei": None, "index": 1})

    def test_cloud_already_selected_at_startup_still_shows_a_new_revision(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "notice_revision", return_value="rev2"),
            patch.object(guard, "_shown_notice_revision", None),
            patch.object(guard, "show_cloud_usage_notice", return_value=True) as notice,
        ):
            wrapped(object(), index=1)
        notice.assert_called_once()

    def test_device_list_refresh_on_cloud_does_not_repop_notice(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "notice_revision", return_value="rev"),
            patch.object(guard, "_shown_notice_revision", None),
            patch.object(guard, "show_cloud_usage_notice", return_value=True) as notice,
        ):
            wrapped(object(), index=1)
            wrapped(object(), index=1)
        notice.assert_called_once()

    def test_internal_no_arg_call_does_not_show_notice(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "notice_dismissed", return_value=False),
            patch.object(guard, "show_cloud_usage_notice") as notice,
        ):
            wrapped(object())
        notice.assert_not_called()

    def test_dismissed_notice_is_not_shown_again(self):
        _, wrapped = self._wrapped()
        with (
            patch.object(guard, "_cloud_selected", return_value=True),
            patch.object(guard, "notice_dismissed", return_value=True),
            patch.object(guard, "_shown_notice_revision", None),
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

    def test_notice_config_is_loaded_once(self):
        with (
            patch.object(guard, "_cached_notice_config", None),
            patch("ok.util.config.Config") as config_cls,
        ):
            config_cls.return_value = {"dont_show_again": False}
            self.assertIs(guard._config(), guard._config())

        config_cls.assert_called_once()


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

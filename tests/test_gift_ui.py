"""Verify the gift page controls keep profile edits connected to its model."""

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from ok.core.start_controller import StartController
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from src.tasks.daily.GiftTask import GiftTask
from src.ui.GiftManagerTab import GiftManagerTab
from tests.support.qt_events import click_at

gift_manager_module = importlib.import_module("src.gifts.GiftManager")


class TestGiftManagerUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_root = Path(os.environ.get("TEMP", "cache/test-tmp"))
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=temp_root)
        root = Path(self.temp_dir.name) / "gift_configs"
        self.patches = [
            patch.object(gift_manager_module, "GIFT_CONFIG_DIR", str(root)),
            patch.object(gift_manager_module, "FRAMES_DIR", str(root / "frames")),
            patch.object(gift_manager_module, "DB_PATH", str(root / "db.json")),
        ]
        for mocked in self.patches:
            mocked.start()
        gift_manager_module.GiftManager._instance = None
        self.manager = gift_manager_module.GiftManager()
        frame = np.full((1080, 1920, 3), 64, dtype=np.uint8)
        self.profile_id = self.manager.create_profile(
            "Synthetic profile", frame, [0], target_count=2, blocked_slots=[1]
        )
        self.page = GiftManagerTab()
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        self.page.dispose()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gift_manager_module.GiftManager._instance = None
        for mocked in reversed(self.patches):
            mocked.stop()
        self.temp_dir.cleanup()

    def test_profile_controls_and_gift_priority_update_the_model(self):
        self.assertEqual(self.page.profile_list.count(), 1)
        self.assertEqual(len(self.page.gift_cards), 10)
        gift_cards = tuple(self.page.gift_cards)

        self.page.enabled_switch.setChecked(False)
        self.page.count_combo.setCurrentIndex(0)
        self.page.name_edit.setText(" Updated name ")
        self.page.name_edit.editingFinished.emit()

        profile = self.manager.get_profile(self.profile_id)
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["target_count"], 1)
        self.assertEqual(profile["display_name"], "Updated name")
        self.assertEqual(self.page.profile_list.currentItem().text(), "Updated name")
        self.assertEqual(self.page.name_edit.text(), "Updated name")
        self.assertEqual(tuple(self.page.gift_cards), gift_cards)

        for card in self.page.gift_cards[:2]:
            click_at(card, card.rect().center())
        profile = self.manager.get_profile(self.profile_id)
        self.assertEqual(profile["selected_slots"], [])
        self.assertEqual(profile["blocked_slots"], [1])

    def test_disposed_page_ignores_a_late_capture(self):
        self.page._capture_pending = True
        self.page.dispose()
        self.page.dispose()
        with patch.object(self.manager, "create_profile") as create:
            self.page._on_capture_done(None, "", "Late result", [], None)
        create.assert_not_called()
        self.assertFalse(self.page._capture_pending)

    def test_capture_uses_controller_queue_and_allows_retry_after_failure_or_stop(self):
        executor = Mock(exclusive_owner=None, current_task=None)
        executor.get_all_tasks.return_value = []
        task = object.__new__(GiftTask)
        task._executor = executor
        task._enabled = task._paused = task.running = False
        task.mode = None
        task.info = {}
        launches = []
        with patch("ok.core.start_controller.Handler") as handler:
            handler.return_value.post.side_effect = launches.append
            controller = StartController({"windows": {"start_exe": False}}, Mock())
        device = Mock()
        device.do_refresh.side_effect = RuntimeError("Device unavailable")
        self.page.task = task
        runtime = SimpleNamespace(start_controller=controller, tr=lambda text: text)

        with (
            patch.object(og, "app", runtime),
            patch.object(og, "executor", executor),
            patch.object(og, "device_manager", device),
            patch.object(controller, "check_auto_hdr"),
            patch.object(controller, "check_gpu_driver_post_processing"),
        ):
            self.page.capture_button.click()
            self.assertTrue(controller.starting)
            self.assertFalse(self.page.capture_button.isEnabled())
            self.assertFalse(self.page.start_button.isEnabled())
            launches.pop()()
            self.page._check_capture_launch()
            executor.enqueue_onetime_task.assert_not_called()
            self.assertTrue(self.page.capture_button.isEnabled())
            self.assertTrue(self.page.recapture_button.isEnabled())
            self.assertIsNone(task.mode)

            device.do_refresh.side_effect = None
            self.page.capture_button.click()
            mark_enabled = controller._mark_task_enabled

            def check_before_enable(launching_task):
                self.assertTrue(controller.starting)
                self.page._capture_launch_check.timeout.emit()
                self.assertEqual(launching_task.mode, GiftTask.MODE_CAPTURE)
                mark_enabled(launching_task)

            with patch.object(controller, "_mark_task_enabled", side_effect=check_before_enable):
                launches.pop()()
            executor.enqueue_onetime_task.assert_called_once_with(task)
            self.assertTrue(self.page.stop_button.isVisible())
            self.page.stop_button.click()
            self.assertTrue(self.page.capture_button.isEnabled())
            self.assertIsNone(task.mode)
            self.assertEqual(self.page.profile_list.count(), 1)

            for button, profile_id, expected_count in (
                (self.page.recapture_button, self.profile_id, 1),
                (self.page.capture_button, None, 2),
            ):
                button.click()
                self.assertEqual(task.capture_profile_id, profile_id)
                launches.pop()()
                self.assertEqual(task.mode, GiftTask.MODE_CAPTURE)
                task.capture_frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
                task.capture_name = "Captured profile"
                task.capture_blocked_slots = (2,)
                task.mode = None
                task.disable()
                self.assertEqual(self.page.profile_list.count(), expected_count)
                profile = self.manager.get_profile(self.page.current_profile_id)
                self.assertEqual(profile["blocked_slots"], [2])
                self.assertTrue(self.page.capture_button.isEnabled())

    def test_gift_controls_resume_without_restarting_and_keep_capture_separate(self):
        task = SimpleNamespace(
            enabled=False, running=False, paused=False, mode=None, first_run_alert=None
        )
        task.run_gift_mode = Mock(side_effect=lambda: setattr(task, "mode", GiftTask.MODE_RUN))
        task.pause = Mock(side_effect=lambda: setattr(task, "paused", True))
        task.unpause = Mock(side_effect=lambda: setattr(task, "paused", False))
        task.disable = Mock(side_effect=lambda: setattr(task, "enabled", False))
        task.capture_profile = Mock()
        self.page.task = task
        start = Mock()
        runtime = SimpleNamespace(
            start_controller=SimpleNamespace(start=start), tr=lambda text: text
        )
        pause, stop, run = self.page.pause_button, self.page.stop_button, self.page.start_button

        with patch.object(og, "app", runtime):
            run.click()
            start.assert_called_once_with(task)

            task.enabled = True
            self.page._on_framework_task_changed(task)
            self.assertFalse(pause.isVisible())
            self.assertTrue(stop.isVisible())
            self.assertFalse(run.isVisible())

            task.running = True
            self.page._on_framework_task_changed(task)
            self.assertTrue(pause.isVisible())
            pause.click()
            task.pause.assert_called_once_with()
            self.assertFalse(pause.isVisible())
            self.assertTrue(run.isVisible())
            self.assertEqual(run.text(), "继续赠礼")
            self.assertFalse(self.page.capture_button.isEnabled())
            self.page._start_capture(None)
            task.capture_profile.assert_not_called()

            with patch.object(self.manager, "get_enabled_profiles", return_value={}):
                run.click()
            task.unpause.assert_called_once_with()
            start.assert_called_once_with(task)
            task.run_gift_mode.assert_called_once_with()
            self.assertTrue(pause.isVisible())

            task.mode = GiftTask.MODE_CAPTURE
            self.page._on_framework_task_changed(task)
            self.assertFalse(pause.isVisible())
            self.assertTrue(stop.isVisible())
            stop.click()
            task.disable.assert_called_once_with()

            task.running = False
            task.mode = None
            self.page._on_framework_task_changed(task)
            self.assertTrue(run.isVisible())
            self.assertTrue(self.page.capture_button.isEnabled())
            self.assertFalse(stop.isVisible())


if __name__ == "__main__":
    unittest.main()

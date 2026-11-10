"""Cloud-only fail-fast diagnostics for the 异象界域 task flow.

Background: opening the F1 panel (探索指南) is the EXPECTED flow, not a stuck
state. A full-screen panel hides the top-right HUD, so stamina OCR reads
nothing at all -- that is "not read", not "insufficient stamina". The old code
ignored the F1 page timeout and reported a misleading "体力不足". These tests
lock in the cloud-specific fail-fast behaviour, keep the local PC variant
byte-for-byte unchanged on those failure paths, and make sure error messages
never tell the user to press ESC and close the correct F1 panel.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import CannotFindException

from src.interaction import cloud_real_input
from src.interaction.NTEInteraction import NTEInteraction
from src.tasks.BaseNTETask import BaseNTETask

BASE = "src.tasks.BaseNTETask._cloud_window_selected"


class _FakeStaminaTask:
    def __init__(self, ocr_boxes=None):
        self.wait_ocr = Mock(return_value=[] if ocr_boxes is None else ocr_boxes)
        self.screenshot = Mock()
        self.log_error = Mock()
        self.info_set = Mock()

    def get_stamina(self):
        return BaseNTETask.get_stamina(self)


class _FakePageTask:
    def __init__(self, reached=False):
        self.openF1panel = Mock()
        self.box_of_screen = Mock(return_value="search_box")
        self.find_one = Mock(return_value=None)
        self.operate_click = Mock()
        self.wait_until = Mock(return_value=reached)
        self.screenshot = Mock()
        self.log_error = Mock()
        self.pos = Mock()
        self.pos.panels.f1.domain.entry = (0.055859, 0.491667)

    def open_f1_domain_page(self):
        return BaseNTETask.open_f1_domain_page(self)


class TestCloudStaminaReading(unittest.TestCase):
    def test_cloud_missing_ocr_raises_instead_of_reporting_insufficient(self):
        task = _FakeStaminaTask()
        with patch(BASE, return_value=True):
            with self.assertRaises(CannotFindException) as ctx:
                task.get_stamina()
        self.assertIn("无法读取体力", str(ctx.exception))
        self.assertIn("这不是「体力不足」", str(ctx.exception))
        self.assertNotIn("按 ESC", str(ctx.exception))
        task.screenshot.assert_called_once_with("stamina_error")
        task.log_error.assert_called_once()
        task.info_set.assert_not_called()

    def test_local_missing_ocr_keeps_legacy_minus_one(self):
        task = _FakeStaminaTask()
        with patch(BASE, return_value=False):
            self.assertEqual(task.get_stamina(), -1)
        task.log_error.assert_not_called()

    def test_cloud_unparsable_ocr_raises(self):
        task = _FakeStaminaTask([SimpleNamespace(name="探索指南")])
        with patch(BASE, return_value=True):
            with self.assertRaises(CannotFindException):
                task.get_stamina()

    def test_local_unparsable_ocr_keeps_legacy_zero(self):
        task = _FakeStaminaTask([SimpleNamespace(name="探索指南")])
        with patch(BASE, return_value=False):
            self.assertEqual(task.get_stamina(), 0)
        task.log_error.assert_not_called()

    def test_zero_stamina_is_a_real_reading_not_a_detection_failure(self):
        for cloud in (False, True):
            with self.subTest(cloud=cloud):
                task = _FakeStaminaTask([SimpleNamespace(name="01240")])
                with patch(BASE, return_value=cloud):
                    self.assertEqual(task.get_stamina(), 0)
                task.log_error.assert_not_called()

    def test_valid_stamina_is_returned_for_both_variants(self):
        for cloud in (False, True):
            with self.subTest(cloud=cloud):
                task = _FakeStaminaTask([SimpleNamespace(name="180l240")])
                with patch(BASE, return_value=cloud):
                    self.assertEqual(task.get_stamina(), 180)
                task.screenshot.assert_not_called()
                task.info_set.assert_called_once_with("当前体力", 180)


class TestCloudF1PageGuard(unittest.TestCase):
    def test_cloud_timeout_raises_and_saves_a_diagnostic_screenshot(self):
        task = _FakePageTask(reached=False)
        with patch(BASE, return_value=True):
            with self.assertRaises(CannotFindException) as ctx:
                task.open_f1_domain_page()
        self.assertIn("未进入异象界域页面", str(ctx.exception))
        self.assertIn("F1 面板已打开", str(ctx.exception))
        self.assertNotIn("按 ESC", str(ctx.exception))
        task.screenshot.assert_called_once_with("f1_domain_page_not_reached")
        task.log_error.assert_called_once()

    def test_local_timeout_keeps_legacy_silent_behaviour(self):
        task = _FakePageTask(reached=False)
        with patch(BASE, return_value=False):
            self.assertFalse(task.open_f1_domain_page())
        task.screenshot.assert_not_called()
        task.log_error.assert_not_called()

    def test_success_returns_reached_for_both_variants(self):
        for cloud in (False, True):
            with self.subTest(cloud=cloud):
                task = _FakePageTask(reached=True)
                with patch(BASE, return_value=cloud):
                    self.assertTrue(task.open_f1_domain_page())
                task.screenshot.assert_not_called()


class TestCloudErrorMessagesAreNotMisleading(unittest.TestCase):
    """F1 opening 探索指南 is expected; never tell the user to ESC out of it."""

    def _both_cloud_error_messages(self):
        messages = []
        with patch(BASE, return_value=True):
            with self.assertRaises(CannotFindException) as ctx:
                _FakeStaminaTask().get_stamina()
            messages.append(str(ctx.exception))
            with self.assertRaises(CannotFindException) as ctx:
                _FakePageTask(reached=False).open_f1_domain_page()
            messages.append(str(ctx.exception))
        return messages

    def test_no_esc_or_main_screen_advice_in_either_message(self):
        messages = self._both_cloud_error_messages()
        self.assertEqual(len(messages), 2)
        for msg in messages:
            with self.subTest(msg=msg):
                self.assertNotIn("按 ESC", msg)
                self.assertNotIn("回到游戏主界面", msg)
                self.assertNotIn("界面不正确", msg)

    def test_messages_point_at_the_real_diagnostics(self):
        for msg in self._both_cloud_error_messages():
            with self.subTest(msg=msg):
                self.assertIn("cloud input: click", msg)
                self.assertIn("screen=", msg)


class _FakeCloudInteraction:
    def __init__(self):
        self._cloud_prepare = Mock(return_value=True)
        self._cloud_set_cursor_pos = Mock()
        self._cloud_pressed_buttons = set()
        self._CLOUD_MIN_DOWN_TIME = 0.05
        self.capture = Mock()
        self.capture.get_abs_cords = Mock(return_value=(1010, 779))

    def _cloud_click(self, x, y, down_time, move, key):
        return NTEInteraction._cloud_click(self, x, y, down_time, move, key)


class TestCloudClickCursorDiagnostics(unittest.TestCase):
    def setUp(self):
        sleep = patch("src.interaction.NTEInteraction.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        mouse = patch.object(cloud_real_input, "mouse_event", return_value=1)
        mouse.start()
        self.addCleanup(mouse.stop)
        jiggle = patch.object(cloud_real_input, "jiggle_mouse", return_value=1)
        jiggle.start()
        self.addCleanup(jiggle.stop)

    def test_cursor_at_target_produces_no_warning(self):
        task = _FakeCloudInteraction()
        with (
            patch.object(cloud_real_input, "get_cursor_pos", return_value=(1010, 779)),
            patch("src.interaction.NTEInteraction.logger.warning") as warning,
        ):
            self.assertTrue(task._cloud_click(89, 442, 0.02, True, "left"))
        warning.assert_not_called()
        task.capture.get_abs_cords.assert_called_once_with(89, 442)

    def test_cursor_pulled_away_warns_once(self):
        task = _FakeCloudInteraction()
        with (
            patch.object(cloud_real_input, "get_cursor_pos", return_value=(1721, 775)),
            patch("src.interaction.NTEInteraction.logger.warning") as warning,
        ):
            self.assertTrue(task._cloud_click(89, 442, 0.02, True, "left"))
            self.assertTrue(task._cloud_click(89, 442, 0.02, True, "left"))
        self.assertEqual(warning.call_count, 1)
        self.assertIn("cursor did not reach", warning.call_args.args[0])

    def test_click_target_is_logged_at_info_so_real_runs_show_it(self):
        # DEBUG may be filtered in a real run; the screen/cursor pair is the
        # primary evidence for "clicks do nothing" on the cloud client.
        task = _FakeCloudInteraction()
        with (
            patch.object(cloud_real_input, "get_cursor_pos", return_value=(1010, 779)),
            patch("src.interaction.NTEInteraction.logger.info") as info,
        ):
            self.assertTrue(task._cloud_click(89, 442, 0.02, True, "left"))
        self.assertTrue(
            any(
                "screen=" in call.args[0] and "cursor=" in call.args[0]
                for call in info.call_args_list
                if call.args
            ),
            info.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()

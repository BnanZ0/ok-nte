import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.daily.DailyActivityTask import DailyActivityTask
from src.tasks.daily.DailyRoutineTask import DailyRoutineTask


class TestDailyActivity(unittest.TestCase):
    def task(self, texts):
        task = object.__new__(DailyActivityTask)
        task.ensure_main = Mock()
        task.open_activity_panel = Mock(return_value=True)
        task.box_of_screen = Mock(return_value=object())
        task.ocr = Mock(return_value=[SimpleNamespace(name=text) for text in texts])
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_error = Mock()
        return task

    def test_full_activity_succeeds(self):
        for text in ("100", "100/100", " 100 / 100 "):
            with self.subTest(text=text):
                task = self.task([text])
                self.assertTrue(task.do_run())
                task.info_set.assert_called_once_with("daily activity", 100)

    def test_incomplete_activity_does_not_succeed(self):
        task = self.task(["80"])
        self.assertFalse(task.do_run())
        task.info_set.assert_called_once_with("daily activity", 80)

    def test_unreadable_or_ambiguous_activity_does_not_succeed(self):
        for texts in ([], ["1000"], ["x100"], ["100", "80"], ["200"]):
            with self.subTest(texts=texts):
                task = self.task(texts)
                self.assertFalse(task.do_run())
                task.info_set.assert_not_called()

    def test_panel_failure_does_not_read_stale_screen(self):
        task = self.task(["100"])
        task.open_activity_panel.return_value = False
        self.assertFalse(task.do_run())
        task.ocr.assert_not_called()

    def test_ocr_error_propagates(self):
        task = self.task([])
        task.ocr.side_effect = RuntimeError("OCR unavailable")
        with self.assertRaisesRegex(RuntimeError, "OCR unavailable"):
            task.do_run()

    def test_routine_registration_is_opt_in(self):
        entry = DailyRoutineTask.entries_by_id()["daily_activity"]
        self.assertIs(entry.task_class, DailyActivityTask)
        self.assertFalse(entry.enabled_by_default)

    @patch("src.tasks.daily.DailyActivityTask.NTEOneTimeTask.run")
    def test_standalone_task_reports_failure(self, initialize):
        task = self.task(["80"])
        with self.assertRaises(RuntimeError):
            task.run()
        initialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()

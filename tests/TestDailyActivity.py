import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ok import CannotFindException

from src.tasks.BaseStaminaTask import BaseStaminaTask, DailyActivity


class TestDailyActivity(unittest.TestCase):
    def _reader(self):
        task = object.__new__(BaseStaminaTask)
        task.open_activity_panel = Mock(return_value=True)
        task.box_of_screen = Mock(return_value="ocr box")
        task.ocr = Mock()
        task.sleep = Mock()
        task.log_info = Mock()
        task.scroll_relative = Mock()
        task.operate = lambda action, **kwargs: action()
        task.info = {"used stamina": 120, "daily activity": 100}
        task.info_set = lambda key, value: task.info.__setitem__(key, value)
        return task

    def test_reader_scrolls_after_unmatched_text_and_returns_observed_progress(self):
        task = self._reader()
        task.ocr.side_effect = [
            [SimpleNamespace(name="40")],
            [SimpleNamespace(name="unmatched")],
            [SimpleNamespace(name="unmatched"), SimpleNamespace(name=" 60 / 180 ")],
        ]

        activity = task.read_daily_activity()

        self.assertEqual(activity, DailyActivity(60, 40))
        self.assertEqual(task.info, {"used stamina": 60, "daily activity": 40})
        task.scroll_relative.assert_called_once()

    def test_missing_ocr_clears_stale_observations(self):
        task = self._reader()
        task.ocr.return_value = None

        activity = task.read_daily_activity()

        self.assertEqual(activity, DailyActivity(None, None))
        self.assertEqual(task.info, {"used stamina": None, "daily activity": None})

    def test_panel_failure_stops_before_ocr(self):
        task = self._reader()
        task.open_activity_panel.return_value = False

        with self.assertRaises(CannotFindException):
            task.read_daily_activity()

        task.ocr.assert_not_called()

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ok import TaskDisabledException

from src.tasks.DSDFarmTask import DSDFarmTask
from src.tasks.dsd_refresh import RefreshIcon


class TestDSDFarmTask(unittest.TestCase):
    def make_task(self):
        task = Mock()
        task.begin_round.side_effect = [True, False]
        task.refresh_monster.side_effect = lambda: DSDFarmTask.refresh_monster(task)
        task.find_confirm.return_value = None
        task.monster_refresh_icon.return_value = RefreshIcon.READY

        def wait_until(condition, **kwargs):
            for _ in range(int(kwargs.get("time_out", 1))):
                result = condition()
                if result:
                    return result
                if kwargs.get("post_action"):
                    kwargs["post_action"]()
            if kwargs.get("raise_if_not_found"):
                raise TimeoutError("Condition not reached")
            return False

        task.wait_until.side_effect = wait_until
        return task

    def test_frozen_cooldown_leaves_waits_and_reopens_before_refresh(self):
        task = self.make_task()
        task.monster_refresh_cooldown.side_effect = [30, None, None, 60]
        waiting_outside = False

        def sleep(seconds):
            nonlocal waiting_outside
            if seconds == 31:
                task.ensure_main.assert_called_once()
                task.operate_click.assert_not_called()
                task.deside_action.assert_not_called()
                waiting_outside = True

        def open_bonfire():
            if task.open_bonfire.call_count == 2:
                self.assertTrue(waiting_outside)

        task.sleep.side_effect = sleep
        task.open_bonfire.side_effect = open_bonfire
        DSDFarmTask.do_run(task)

        self.assertEqual(task.open_bonfire.call_count, 2)
        task.operate_click.assert_called_once()
        task.deside_action.assert_called_once()
        task.add_success.assert_called_once()

    def test_ready_refresh_without_confirmation(self):
        task = self.make_task()
        task.monster_refresh_cooldown.side_effect = [None, None, 60]
        DSDFarmTask.do_run(task)
        task.wait_click_confirm.assert_not_called()
        task.deside_action.assert_called_once()

    def test_refresh_with_confirmation(self):
        task = self.make_task()
        task.monster_refresh_cooldown.side_effect = [None, None, 59]
        task.find_confirm.return_value = Mock()
        DSDFarmTask.do_run(task)
        task.wait_click_confirm.assert_called_once()
        task.deside_action.assert_called_once()

    def test_unchanged_button_does_not_depart(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        with self.assertRaises(TimeoutError):
            DSDFarmTask.do_run(task)
        task.deside_action.assert_not_called()
        task.add_success.assert_not_called()

    def test_confirmation_without_new_cooldown_does_not_depart(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        task.find_confirm.return_value = Mock()
        with self.assertRaises(TimeoutError):
            DSDFarmTask.do_run(task)
        task.deside_action.assert_not_called()

    def test_cooldown_wait_can_be_cancelled(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = 30
        task.sleep.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            DSDFarmTask.do_run(task)
        task.operate_click.assert_not_called()
        task.deside_action.assert_not_called()

    def test_second_read_catches_missed_cooldown(self):
        task = self.make_task()
        task.monster_refresh_cooldown.side_effect = [None, 10, None, None, 60]
        DSDFarmTask.do_run(task)
        task.sleep.assert_any_call(11)
        task.operate_click.assert_called_once()

    def test_parse_cooldown(self):
        for text, expected in [("60s", 60), (" 9 S ", 9), ("0s", 0), ("59", 59),
                               ("61s", None), ("刷新怪物", None), ("", None)]:
            with self.subTest(text=text):
                task = Mock()
                task.ocr.return_value = [SimpleNamespace(name=text)]
                self.assertEqual(DSDFarmTask.monster_refresh_cooldown(task), expected)

    def test_visual_fallback_confirms_two_cooldown_frames(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        task.monster_refresh_icon.side_effect = [
            RefreshIcon.READY, RefreshIcon.COOLDOWN, RefreshIcon.COOLDOWN,
        ]
        DSDFarmTask.do_run(task)
        task.operate_click.assert_called_once()
        task.deside_action.assert_called_once()

    def test_unknown_state_waits_once_then_stops_without_clicking(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        task.monster_refresh_icon.return_value = RefreshIcon.UNKNOWN
        with self.assertRaises(TimeoutError):
            DSDFarmTask.do_run(task)
        task.sleep.assert_any_call(61)
        self.assertEqual(task.open_bonfire.call_count, 2)
        task.operate_click.assert_not_called()
        task.deside_action.assert_not_called()

    def test_unknown_state_can_recover_after_wait(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        task.monster_refresh_icon.side_effect = [
            RefreshIcon.UNKNOWN, RefreshIcon.READY,
            RefreshIcon.COOLDOWN, RefreshIcon.COOLDOWN,
        ]
        DSDFarmTask.do_run(task)
        task.sleep.assert_any_call(61)
        task.deside_action.assert_called_once()

    def test_single_cooldown_frame_is_not_success(self):
        task = self.make_task()
        task.monster_refresh_cooldown.return_value = None
        states = iter([RefreshIcon.READY, RefreshIcon.COOLDOWN, RefreshIcon.UNKNOWN])
        task.monster_refresh_icon.side_effect = lambda: next(states, RefreshIcon.UNKNOWN)
        with self.assertRaises(TimeoutError):
            DSDFarmTask.do_run(task)
        task.deside_action.assert_not_called()

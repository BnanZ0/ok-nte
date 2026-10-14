import unittest
from unittest.mock import Mock

from src.combat.BaseCombatTask import SleepCheckSkip
from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.BaseStaminaTask import BaseStaminaTask, DailyActivity


class TestStaminaTask(unittest.TestCase):
    def test_remaining_stamina_uses_daily_consumption_and_clamps_at_zero(self):
        for used, target, expected in ((0, 180, 180), (60, 120, 60), (120, 120, 0), (120, 60, 0)):
            with self.subTest(used=used, target=target):
                self.assertEqual(
                    BaseStaminaTask.calculate_remaining_stamina(DailyActivity(used, 40), target),
                    expected,
                )

    def test_daily_completion_skips_even_when_the_other_observation_is_missing(self):
        for activity in (DailyActivity(180, None), DailyActivity(None, 100)):
            with self.subTest(activity=activity):
                self.assertTrue(BaseStaminaTask.daily_activity_complete(activity))
                self.assertEqual(BaseStaminaTask.calculate_remaining_stamina(activity, 240), 0)

    def test_satisfied_user_target_needs_no_additional_observations(self):
        self.assertEqual(
            BaseStaminaTask.calculate_remaining_stamina(DailyActivity(120, None), 120), 0
        )
        self.assertEqual(
            BaseStaminaTask.calculate_remaining_stamina(DailyActivity(None, None), 0), 0
        )

    def test_unknown_progress_cannot_be_treated_as_zero(self):
        for activity in (
            DailyActivity(None, 40),
            DailyActivity(60, None),
            DailyActivity(None, None),
        ):
            with self.subTest(activity=activity):
                with self.assertRaises(ValueError):
                    BaseStaminaTask.calculate_remaining_stamina(activity, 180)

    def _task(self):
        task = object.__new__(AnomalyTask)
        task.config = {BaseStaminaTask.CONF_STAMINA_TARGET: 120}
        task.sleep_check_skip = SleepCheckSkip()
        task.check_combat = Mock()
        task.info_set = Mock()
        task.log_info = Mock()
        task.read_daily_activity = Mock(return_value=DailyActivity(60, 40))
        return task

    def test_daily_preparation_uses_own_config_without_changing_the_saved_target(self):
        task = self._task()

        self.assertEqual(task.resolve_stamina_target(), 60)

        self.assertEqual(task.config[task.CONF_STAMINA_TARGET], 120)
        self.assertTrue(task.sleep_check_skip.all)

    def test_target_resolution_distinguishes_daily_and_reward_count_modes(self):
        task = self._task()
        self.assertEqual(task.resolve_stamina_target(), 60)
        self.assertEqual(task.config[task.CONF_STAMINA_TARGET], 120)
        task.config[task.CONF_STAMINA_TARGET] = 0
        task.read_daily_activity.reset_mock()
        self.assertEqual(task.resolve_stamina_target(), 0)
        task.read_daily_activity.assert_not_called()
        task.config.clear()
        self.assertIsNone(task.resolve_stamina_target())
        task.read_daily_activity.assert_not_called()

    def test_panel_failure_keeps_sleep_checks_disabled(self):
        task = self._task()

        def fail_read():
            self.assertTrue(task.sleep_check_skip.all)
            task.sleep_check()
            raise ValueError("activity panel unavailable")

        task.read_daily_activity.side_effect = fail_read

        with self.assertRaises(ValueError):
            task.resolve_stamina_target()

        self.assertTrue(task.sleep_check_skip.all)
        task.check_combat.assert_not_called()

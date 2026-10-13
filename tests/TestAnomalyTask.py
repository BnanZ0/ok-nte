import unittest
from unittest.mock import Mock

from src.tasks.AnomalyTask import AnomalyTask


class FakeDailyTask(AnomalyTask):
    def __init__(self, config):
        self.config = config
        self.sync_config = Mock()
        self.log_info = Mock()
        self.log_warning = Mock()


class TestAnomalyTask(unittest.TestCase):
    def test_shift_id_custom_cycle_keeps_friendly_experience_labels_and_sets_next_task(self):
        self.assertIn(AnomalyTask.EXP_CHAR, AnomalyTask.CYCLE_CUSTOM_OPTIONS)
        self.assertNotIn(
            AnomalyTask.CYCLE_CUSTOM_OPTION_FMT.format(task=AnomalyTask.TASK_EXP_COIN, id=1),
            AnomalyTask.CYCLE_CUSTOM_OPTIONS,
        )
        daily = FakeDailyTask(
            {
                AnomalyTask.CONF_CYCLEB_TASK_MODE: AnomalyTask.CYCLE_CUSTOM,
                AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_EXP_COIN,
                AnomalyTask.CONF_EXP_TARGET: AnomalyTask.EXP_ARC,
                AnomalyTask.CONF_CUSTOM_CYCLE: [
                    AnomalyTask.EXP_ARC,
                    AnomalyTask.CYCLE_CUSTOM_OPTION_FMT.format(task=AnomalyTask.TASK_ABILITY, id=5),
                ],
            }
        )

        daily.on_daily_end(True)

        self.assertEqual(daily.config[AnomalyTask.CONF_TASK_TYPE], AnomalyTask.TASK_ABILITY)
        self.assertEqual(daily.config[AnomalyTask.CONF_ABILITY_ID], 5)
        daily.sync_config.assert_called_once_with()

    def test_custom_cycle_uses_first_option_when_current_task_is_not_in_cycle(self):
        daily = FakeDailyTask(
            {
                AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_EXP_COIN,
                AnomalyTask.CONF_EXP_TARGET: AnomalyTask.EXP_CHAR,
                AnomalyTask.CONF_CUSTOM_CYCLE: [
                    AnomalyTask.CYCLE_CUSTOM_OPTION_FMT.format(task=AnomalyTask.TASK_ARC, id=3),
                ],
            }
        )

        daily.shift_custom_cycle()

        self.assertEqual(daily.config[AnomalyTask.CONF_TASK_TYPE], AnomalyTask.TASK_ARC)
        self.assertEqual(daily.config[AnomalyTask.CONF_ARC_ID], 3)
        daily.sync_config.assert_called_once_with()

    def test_custom_cycle_ignores_empty_or_invalid_cycle_entries(self):
        for cycle in ([], ["无效循环项"]):
            with self.subTest(cycle=cycle):
                daily = FakeDailyTask(
                    {
                        AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_EXP_COIN,
                        AnomalyTask.CONF_EXP_TARGET: AnomalyTask.EXP_CHAR,
                        AnomalyTask.CONF_CUSTOM_CYCLE: cycle,
                    }
                )

                daily.shift_custom_cycle()

                self.assertEqual(
                    daily.config[AnomalyTask.CONF_TASK_TYPE], AnomalyTask.TASK_EXP_COIN
                )
                daily.sync_config.assert_not_called()

    def test_shift_id_dispatches_sub_task_cycle(self):
        daily = FakeDailyTask(
            {
                AnomalyTask.CONF_CYCLEB_TASK_MODE: AnomalyTask.CYCLE_SUB_TASK,
                AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_ABILITY,
                AnomalyTask.CONF_ABILITY_ID: 5,
            }
        )

        daily.on_daily_end(True)

        self.assertEqual(daily.config[AnomalyTask.CONF_ABILITY_ID], 1)
        daily.sync_config.assert_called_once_with()

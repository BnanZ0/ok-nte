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
    def setUp(self):
        self.anomaly = object.__new__(AnomalyTask)
        self.anomaly.log_warning = Mock()
        self.anomaly.sync_config = Mock()

    def test_get_sub_idx_uses_id_minus_one_for_all_task_types(self):
        configs = (
            (
                {
                    AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_EXP_COIN,
                    AnomalyTask.CONF_EXP_TARGET: AnomalyTask.EXP_ARC,
                },
                1,
            ),
            (
                {
                    AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_ABILITY,
                    AnomalyTask.CONF_ABILITY_ID: 5,
                },
                4,
            ),
            (
                {
                    AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_CONSOLE,
                    AnomalyTask.CONF_CONSOLE_ID: 6,
                },
                5,
            ),
        )

        for config, expected_idx in configs:
            with self.subTest(config=config):
                self.anomaly.config = config
                self.assertEqual(self.anomaly.get_sub_idx(), expected_idx)

    def test_drop_down_options_are_lists_of_strings(self):
        self.assertIsInstance(AnomalyTask.TASK_TYPES, list)
        self.assertIsInstance(AnomalyTask.EXP_TARGET_OPTIONS, list)
        self.assertTrue(all(isinstance(option, str) for option in AnomalyTask.TASK_TYPES))
        self.assertTrue(all(isinstance(option, str) for option in AnomalyTask.EXP_TARGET_OPTIONS))

    def test_resolve_sub_id_clamps_numeric_ids_and_normalizes_config(self):
        config = {
            AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_ABILITY,
            AnomalyTask.CONF_ABILITY_ID: 99,
        }

        self.anomaly.config = config
        self.assertEqual(self.anomaly.resolve_sub_id(), 5)
        self.assertEqual(config[AnomalyTask.CONF_ABILITY_ID], 5)
        self.anomaly.sync_config.assert_called_once_with()

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
                daily.log_warning.assert_called_once()

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

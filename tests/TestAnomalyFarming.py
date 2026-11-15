import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ok.core.config_schema import config_visibility

from src.tasks.anomaly_rotation import RotationTarget, StaminaRotation
from src.tasks.anomaly_statistics import FarmingStatistics
from src.tasks.AnomalyTask import AnomalyTask
from tests import TestAnomalyRotation as rotation_tests


class TestFarmingConfiguration(unittest.TestCase):
    def schema(self, daily=False):
        result = SimpleNamespace(default_config={}, config_type={}, config_description={})
        result.add_claim_reward_count_config = Mock()
        AnomalyTask.setup_config(result, daily=daily)
        return result

    def test_material_ids_are_visible_only_for_fixed_target(self):
        for daily in (False, True):
            schema = self.schema(daily)
            config = schema.default_config
            config[AnomalyTask.CONF_TASK_TYPE] = AnomalyTask.TASK_ABILITY
            for strategy in (
                AnomalyTask.STRATEGY_FIXED,
                AnomalyTask.STRATEGY_SAME_TYPE,
                AnomalyTask.STRATEGY_CUSTOM,
            ):
                config[AnomalyTask.CONF_STRATEGY] = strategy
                visible = config_visibility(config, schema.config_type)
                self.assertEqual(
                    visible(AnomalyTask.CONF_ABILITY_ID), strategy == AnomalyTask.STRATEGY_FIXED
                )
                self.assertEqual(
                    visible(AnomalyTask.CONF_TASK_TYPE), strategy != AnomalyTask.STRATEGY_CUSTOM
                )
                self.assertEqual(
                    visible(AnomalyTask.CONF_ROTATION_SEQUENCE),
                    strategy == AnomalyTask.STRATEGY_CUSTOM,
                )

    def test_stamina_fields_follow_selected_mode(self):
        for daily in (False, True):
            schema = self.schema(daily)
            config = schema.default_config
            for mode in schema.config_type[AnomalyTask.CONF_STAMINA_MODE]["options"]:
                config[AnomalyTask.CONF_STAMINA_MODE] = mode
                visible = config_visibility(config, schema.config_type)
                self.assertEqual(
                    visible(AnomalyTask.CONF_STAMINA_LIMIT), mode == AnomalyTask.STAMINA_LIMIT
                )
                if daily:
                    self.assertEqual(
                        visible(AnomalyTask.CONF_STAMINA_TARGET), mode == AnomalyTask.STAMINA_DAILY
                    )

    def test_default_modes_preserve_existing_manual_and_daily_behavior(self):
        self.assertEqual(
            self.schema().default_config[AnomalyTask.CONF_STAMINA_MODE], AnomalyTask.STAMINA_ALL
        )
        self.assertEqual(
            self.schema(True).default_config[AnomalyTask.CONF_STAMINA_MODE],
            AnomalyTask.STAMINA_DAILY,
        )

    def test_advanced_and_legacy_cycle_options_are_hidden_until_relevant(self):
        schema = self.schema(True)
        config = schema.default_config
        config[AnomalyTask.CONF_STRATEGY] = AnomalyTask.STRATEGY_CUSTOM
        visible = config_visibility(config, schema.config_type)
        self.assertFalse(visible(AnomalyTask.CONF_QUOTA))
        config[AnomalyTask.CONF_ADVANCED] = True
        self.assertTrue(visible(AnomalyTask.CONF_QUOTA))
        self.assertFalse(visible(AnomalyTask.CONF_CYCLEB_TASK_MODE))
        config[AnomalyTask.CONF_STRATEGY] = AnomalyTask.STRATEGY_FIXED
        self.assertTrue(visible(AnomalyTask.CONF_CYCLEB_TASK_MODE))
        config[AnomalyTask.CONF_CYCLEB_TASK_MODE] = AnomalyTask.CYCLE_CUSTOM
        self.assertTrue(visible(AnomalyTask.CONF_CUSTOM_CYCLE))
        self.assertFalse(visible(AnomalyTask.CONF_QUOTA))


class TestFarmingExecution(unittest.TestCase):
    def task(self, strategy=AnomalyTask.STRATEGY_CUSTOM):
        return rotation_tests.TestAnomalyRotation().task(strategy)

    def game(self, task, balance):
        return rotation_tests.TestAnomalyRotation().mock_game(task, balance)

    def test_all_stamina_mode_does_not_read_completed_daily_activity(self):
        task = self.task()
        task.config[task.CONF_STAMINA_MODE] = task.STAMINA_ALL
        task.config[task.CONF_STAMINA_TARGET] = 0
        task.read_daily_activity = Mock(
            side_effect=AssertionError("Must not inspect daily progress")
        )
        task.resolve_stamina_target = AnomalyTask.resolve_stamina_target.__get__(task)
        self.game(task, 200)
        self.assertTrue(task.do_run())
        task.read_daily_activity.assert_not_called()

    def test_explicit_budget_never_rounds_above_the_limit(self):
        task = self.task()
        task.config[task.CONF_STAMINA_MODE] = task.STAMINA_LIMIT
        task.config[task.CONF_STAMINA_LIMIT] = 100
        task.resolve_stamina_target = AnomalyTask.resolve_stamina_target.__get__(task)
        balance, _ = self.game(task, 500)
        self.assertTrue(task.do_run())
        self.assertEqual(balance[0], 420)

    def test_fixed_target_reads_available_stamina_again_before_finishing(self):
        task = self.task(AnomalyTask.STRATEGY_FIXED)
        _, visits = self.game(task, 200)
        self.assertTrue(task.do_run())
        self.assertEqual([units for _, _, units in visits], [5, 0])

    def test_running_rotation_does_not_change_saved_fixed_target(self):
        task = self.task()
        original = {
            key: task.config[key]
            for key in (task.CONF_TASK_TYPE, task.CONF_ABILITY_ID, task.CONF_CONSOLE_ID)
        }
        self.game(task, 200)
        self.assertTrue(task.do_run())
        self.assertEqual({key: task.config[key] for key in original}, original)

    def test_hidden_material_id_does_not_select_rotation_start(self):
        task = self.task(AnomalyTask.STRATEGY_SAME_TYPE)
        task.config[task.CONF_ABILITY_ID] = 5
        self.assertEqual(task.build_rotation().current.task_id, 1)

    def test_five_hundred_stamina_queue_resumes_half_completed_console(self):
        task = self.task()
        task.config[task.CONF_ROTATION_SEQUENCE] = [
            task.get_cycle_option(task.TASK_ABILITY, 1),
            task.get_cycle_option(task.TASK_CONSOLE, 1),
            task.get_cycle_option(task.TASK_ABILITY, 2),
        ]
        task.config[task.CONF_QUOTAS] = "120,120,80"
        balance, visits = self.game(task, 500)
        self.assertTrue(task.do_run())
        self.assertEqual(balance[0], 0)
        rotation = task.build_rotation()
        self.assertEqual(
            (rotation.current.task_type, rotation.current.task_id, rotation.spent),
            (task.TASK_CONSOLE, 1, 60),
        )
        self.assertEqual(task.config[task.CONF_TOTALS][rotation.current.option]["stamina"], 180)
        resumed = self.task()
        resumed.config = json.loads(json.dumps(task.config))
        balance, visits = self.game(resumed, 60)
        self.assertTrue(resumed.do_run())
        self.assertEqual(visits[0], (task.TASK_CONSOLE, 1, 1))
        self.assertEqual(resumed.build_rotation().current.task_id, 2)

    def test_missing_claim_callback_stops_instead_of_repeating_forever(self):
        task = self.task()
        task.run_target = Mock(return_value=(True, 2))
        with self.assertRaises(ValueError):
            task.do_run()
        self.assertEqual(task.run_target.call_count, 1)


class TestFarmingPersistence(unittest.TestCase):
    def test_statistics_count_confirmed_units_and_reject_corrupt_values(self):
        stats = FarmingStatistics({"bad": {"single": True, "double": 0, "stamina": 40}})
        self.assertFalse(stats.targets)
        stats.record("空幕1", 2, 60)
        stats.record("空幕1", 1, 60)
        self.assertEqual(stats.snapshot(), {"空幕1": {"single": 1, "double": 1, "stamina": 180}})
        with self.assertRaises(ValueError):
            stats.record("空幕1", True, 60)

    def test_checkpoint_detects_a_silent_save_failure(self):
        task = object.__new__(AnomalyTask)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AnomalyTask.json"
            path.write_text("{}", encoding="utf-8")

            class SilentFailure(dict):
                config_file = str(path)

                def save_file(self):
                    pass

            task.config = SilentFailure()
            with self.assertRaises(RuntimeError):
                task.persist_checkpoint({task.CONF_ROTATION_STATE: {"index": 1}})

    def test_daily_checkpoint_and_csv_use_the_daily_store(self):
        from ok.util.config import Config

        from src.tasks.daily.DailyRoutineTask import _DailyTaskConfig

        task = object.__new__(AnomalyTask)
        task.validate = Mock(return_value=(True, None))
        task.log_warning = Mock()
        with tempfile.TemporaryDirectory() as directory:
            routine = SimpleNamespace(
                routine_task_configs=Config(
                    "DailyRoutineTaskConfigs",
                    {"daily_anomaly": {}},
                    folder=str(Path(directory) / "configs"),
                )
            )
            task.config = _DailyTaskConfig(
                routine, "daily_anomaly", task, {task.CONF_ROTATION_STATE: {}, task.CONF_TOTALS: {}}
            )
            updates = {task.CONF_ROTATION_STATE: {"index": 1}, task.CONF_TOTALS: {"test": 80}}
            task.persist_checkpoint(updates)
            saved = json.loads(Path(routine.routine_task_configs.config_file).read_text("utf-8"))
            self.assertEqual(saved["daily_anomaly"], updates)
            task.write_claim_log("空幕: 1", 2, 60)
            with (Path(directory) / "stats/anomaly_claims.csv").open(
                encoding="utf-8-sig"
            ) as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1][-3:], ["空幕: 1", "2", "120"])

    def test_invalid_target_and_saved_state_cannot_overrun_a_quota(self):
        for quota in (0, -40, 41, True):
            with self.assertRaises(ValueError):
                RotationTarget("a", "ability", 1, 40, quota)
        target = RotationTarget("a", "ability", 1, 40, 80)
        saved = {"plan": [["a", 40, 80]], "index": True, "spent": 40}
        rotation = StaminaRotation([target], saved)
        self.assertEqual(rotation.spent, 0)

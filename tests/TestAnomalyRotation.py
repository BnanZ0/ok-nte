import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ok import Box, TaskDisabledException

from src.tasks.anomaly_rotation import RotationTarget, StaminaRotation, rounded_quota
from src.tasks.AnomalyTask import AnomalyTask


class TestRotationPlanner(unittest.TestCase):
    def targets(self):
        return [
            RotationTarget("a", "ability", 1, 40, 80),
            RotationTarget("b", "console", 2, 60, 120),
        ]

    def test_quota_uses_dungeon_cost_and_rounds_minimum_up(self):
        for minimum, cost, expected in (
            (0, 40, 80),
            (0, 60, 120),
            (80, 60, 120),
            (1, 60, 60),
            (121, 40, 160),
        ):
            with self.subTest(minimum=minimum, cost=cost):
                self.assertEqual(rounded_quota(minimum, cost), expected)

    def test_round_robin_resumes_partial_quota_after_serialization(self):
        rotation = StaminaRotation(self.targets())
        rotation.record_claim(2)
        rotation.record_claim(1)
        saved = json.loads(json.dumps(rotation.snapshot()))
        resumed = StaminaRotation(self.targets(), saved)
        self.assertEqual((resumed.current.option, resumed.spent, resumed.remaining), ("b", 60, 60))
        resumed.record_claim(1)
        self.assertEqual((resumed.current.option, resumed.spent), ("a", 0))

    def test_configuration_changes_and_corrupt_state_start_from_selected_target(self):
        for saved in (
            {"plan": [], "index": 1, "spent": 40},
            {
                "plan": [[t.option, t.cost, t.quota] for t in self.targets()],
                "index": 1,
                "spent": 40,
            },
            {
                "plan": [[t.option, t.cost, t.quota] for t in self.targets()],
                "index": -1,
                "spent": 0,
            },
        ):
            with self.subTest(saved=saved):
                rotation = StaminaRotation(self.targets(), saved, "b")
                self.assertEqual((rotation.current.option, rotation.spent), ("b", 0))

    def test_claim_cannot_exceed_quota(self):
        rotation = StaminaRotation(self.targets())
        rotation.record_claim(1)
        with self.assertRaises(ValueError):
            rotation.record_claim(2)
        self.assertEqual(rotation.spent, 40)


class TestAnomalyRotation(unittest.TestCase):
    def task(self, strategy=AnomalyTask.STRATEGY_CUSTOM):
        task = object.__new__(AnomalyTask)
        task.config = {
            task.CONF_TASK_TYPE: task.TASK_ABILITY,
            task.CONF_ABILITY_ID: 1,
            task.CONF_ARC_ID: 1,
            task.CONF_CONSOLE_ID: 1,
            task.CONF_STRATEGY: strategy,
            task.CONF_ROTATION_SEQUENCE: [
                task.get_cycle_option(task.TASK_ABILITY, 1),
                task.get_cycle_option(task.TASK_CONSOLE, 2),
            ],
        }
        for method in ("ensure_main", "info_set", "log_info", "log_warning", "shift_id"):
            setattr(task, method, Mock())
        task.resolve_stamina_target = Mock(return_value=None)
        return task

    def mock_game(self, task, stamina):
        balance = [stamina]
        visits = []

        def run_target(budget, reward_limit, on_claim, *, target, round_up):
            cost = target.cost
            planned = balance[0] // cost
            if budget is not None:
                budget_units = (budget + cost - 1) // cost if round_up else budget // cost
                planned = min(planned, budget_units)
            if reward_limit is not None:
                planned = min(planned, reward_limit)
            visits.append((target.task_type, target.task_id, planned))
            completed = 0
            while completed < planned:
                units = min(2, planned - completed)
                balance[0] -= cost * units
                completed += units
                on_claim(units)
            return True, completed

        task.run_target = Mock(side_effect=run_target)
        return balance, visits

    def test_mixed_dungeons_spend_correct_cost_and_resume_across_runs(self):
        task = self.task()
        balance, visits = self.mock_game(task, 140)
        self.assertTrue(task.do_run())
        self.assertEqual(balance[0], 0)
        self.assertEqual(visits[:2], [(task.TASK_ABILITY, 1, 2), (task.TASK_CONSOLE, 2, 1)])
        saved_config = json.loads(json.dumps(task.config))
        resumed = self.task()
        resumed.config = saved_config
        balance, visits = self.mock_game(resumed, 140)
        self.assertTrue(resumed.do_run())
        self.assertEqual(visits[:2], [(task.TASK_CONSOLE, 2, 1), (task.TASK_ABILITY, 1, 2)])
        self.assertEqual(balance[0], 0)

    def test_total_reward_limit_counts_double_as_two_units(self):
        task = self.task()
        task.config[task.CONF_CLAIM_REWARD_COUNT] = 3
        balance, visits = self.mock_game(task, 500)
        self.assertTrue(task.do_run())
        self.assertEqual(balance[0], 360)
        self.assertEqual(len(visits), 2)
        self.assertEqual(task.build_rotation().spent, 60)

    def test_daily_budget_preserves_rounding_and_does_not_shift_twice(self):
        task = self.task()
        task.config[task.CONF_STAMINA_MODE] = task.STAMINA_DAILY
        task.resolve_stamina_target.return_value = 100
        balance, _ = self.mock_game(task, 500)
        self.assertTrue(task.do_run())
        self.assertEqual(balance[0], 360)  # 80 + one 60 reward reaches the daily minimum.
        task.on_daily_end(True)
        task.shift_id.assert_not_called()

    def test_failed_or_interrupted_claim_preserves_last_confirmed_progress(self):
        for interruption in (False, True):
            task = self.task()

            def run_target(_budget, _limit, on_claim, **kwargs):
                on_claim(1)
                if interruption:
                    raise TaskDisabledException()
                return False, 1

            task.run_target = Mock(side_effect=run_target)
            if interruption:
                with self.assertRaises(TaskDisabledException):
                    task.do_run()
            else:
                self.assertFalse(task.do_run())
            self.assertEqual(task.build_rotation().spent, 40)

    def test_custom_quotas_and_same_type_sequence(self):
        task = self.task()
        task.config[task.CONF_QUOTAS] = "120,80"
        self.assertEqual([t.quota for t in task.build_rotation().targets], [120, 120])
        task.config[task.CONF_STRATEGY] = task.STRATEGY_SAME_TYPE
        task.config[task.CONF_QUOTAS] = ""
        self.assertEqual([t.task_id for t in task.build_rotation().targets], [1, 2, 3, 4, 5])

    def test_invalid_sequence_and_quota_fail_before_game_input(self):
        for options, quotas in (([], ""), (["unknown"], ""), (None, "1"), (None, "-1,80")):
            task = self.task()
            if options is not None:
                task.config[task.CONF_ROTATION_SEQUENCE] = options
            task.config[task.CONF_QUOTAS] = quotas
            with self.assertRaises(ValueError):
                task.do_run()
            task.ensure_main.assert_not_called()

    def test_skipped_daily_does_not_discard_reset_request(self):
        task = self.task()
        task.config[task.CONF_RESET_ROTATION] = True
        task.resolve_stamina_target.return_value = 0
        self.assertIsNone(task.do_run())
        self.assertTrue(task.config[task.CONF_RESET_ROTATION])

    def test_schema_exposes_sequence_in_both_task_contexts(self):
        from ok.core.config_schema import config_visibility

        for daily in (False, True):
            schema = SimpleNamespace(default_config={}, config_type={}, config_description={})
            schema.add_claim_reward_count_config = Mock()
            AnomalyTask.setup_config(schema, daily=daily)
            schema.default_config[AnomalyTask.CONF_STRATEGY] = AnomalyTask.STRATEGY_CUSTOM
            visible = config_visibility(schema.default_config, schema.config_type)
            self.assertTrue(visible(AnomalyTask.CONF_ROTATION_SEQUENCE))
            self.assertFalse(visible(AnomalyTask.CONF_QUOTA))
            schema.default_config[AnomalyTask.CONF_ADVANCED] = True
            self.assertTrue(visible(AnomalyTask.CONF_QUOTA))

    def claim_task(self, boxes):
        task = self.task()
        for method in (
            "walk_until_combat",
            "combat_once",
            "rotate_and_find_treasure",
            "operate_click",
        ):
            setattr(task, method, Mock())
        task.retry_on_action = Mock(return_value=boxes)
        task.width_of_screen = Mock(return_value=500)
        task.box_of_screen = Mock()
        task.find_confirm = Mock(return_value=True)
        task.wait_until = Mock(side_effect=lambda action, **_: action())
        return task

    def test_target_runner_uses_sixty_cost_and_limits_double_claims(self):
        for dungeon, stamina, budget, expected in (
            (AnomalyTask.TASK_CONSOLE, 180, None, [True, False]),
            (AnomalyTask.TASK_CONSOLE, 120, 60, [False]),
            (AnomalyTask.TASK_ABILITY, 200, 120, [True, False]),
        ):
            task = self.task()
            task.config[task.CONF_TASK_TYPE] = dungeon
            task.pos = SimpleNamespace(
                panels=SimpleNamespace(
                    f1=SimpleNamespace(
                        domain=SimpleNamespace(exp=(1, 1), skill=(1, 1), arc=(1, 1), console=(1, 1))
                    )
                )
            )
            for method in (
                "open_f1_domain_page",
                "sleep",
                "operate_click",
                "click_traval_button",
                "wait_in_team",
                "enter_anomaly_from_interac",
                "wait_click_confirm",
                "box_of_screen",
            ):
                setattr(task, method, Mock())
            task.get_stamina = Mock(return_value=stamina)
            task.find_confirms = Mock(return_value=[Box(10, 10, 20, 20)])
            task.find_exit = Mock(return_value=True)
            task.wait_until = Mock(side_effect=lambda action, **_: action())
            task.do_combat_and_claim = Mock(side_effect=lambda double: 2 if double else 1)
            receipt = Mock()
            success, completed = task.run_target(budget, None, receipt)
            self.assertTrue(success)
            self.assertEqual([c.args[0] for c in task.do_combat_and_claim.call_args_list], expected)
            self.assertEqual(sum(c.args[0] for c in receipt.call_args_list), completed)

    def test_double_claim_selection_and_single_fallback_return_actual_units(self):
        left, right = Box(300, 650, 20, 20), Box(600, 650, 20, 20)
        for double, boxes, expected in (
            (True, [left, right], 2),
            (True, [left], 1),
            (False, [left, right], 1),
        ):
            task = self.claim_task(boxes)
            self.assertEqual(task.do_combat_and_claim(double), expected)
            task.find_confirm.assert_called_once()

    def test_missing_single_button_does_not_click_double_or_advance(self):
        task = self.claim_task([Box(600, 650, 20, 20)])
        with self.assertRaises(ValueError):
            task.do_combat_and_claim(False)
        task.operate_click.assert_not_called()

    def test_uncertain_claim_stops_instead_of_reporting_success(self):
        task = self.claim_task([Box(300, 650, 20, 20)])
        task.wait_until.side_effect = TimeoutError("settlement not found")
        with self.assertRaises(TimeoutError):
            task.do_combat_and_claim(False)


if __name__ == "__main__":
    unittest.main()

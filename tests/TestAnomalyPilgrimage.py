import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ok import CannotFindException

from src.combat.BaseCombatTask import TeamSurvivalStatus
from src.tasks.AnomalyPilgrimage import AnomalyPilgrimage


class FakePilgrimage(AnomalyPilgrimage):
    def __init__(self, targets=("A",), *, stamina=180, remaining=3):
        self.config = {self.CONF_TARGET: list(targets)}
        self.info = {}
        self.open_target_list = Mock()
        self.read_remaining_attempts = Mock(return_value=remaining)
        self.get_stamina = Mock(return_value=stamina)
        self.enter_target = Mock(return_value=True)
        self.fight_target_once = Mock(return_value=TeamSurvivalStatus.NO_DEATHS)
        self.retry_target = Mock(return_value=True)
        self.claim_target_reward = Mock(return_value=True)
        self.return_to_target_list = Mock(return_value=True)
        self.exit_target = Mock(return_value=True)
        self.log_info = Mock()
        self.log_warning = Mock()

    def info_set(self, key, value):
        self.info[key] = value


class TestAnomalyPilgrimage(unittest.TestCase):
    def setUp(self):
        templates = self.enterContext(patch("src.tasks.AnomalyPilgrimage.WEEKLYBOSS_ITEM"))
        templates.list_names.return_value = ("A", "B", "C", "D", "E")

    def set_run_date(self, day):
        now = self.enterContext(patch("src.tasks.AnomalyPilgrimage.datetime"))
        now.now.return_value.astimezone.return_value.date.return_value = day

    def test_rotation_settings_are_daily_only(self):
        task = AnomalyPilgrimage
        for daily in (False, True):
            with self.subTest(daily=daily):
                schema = SimpleNamespace(default_config={}, config_type={}, config_description={})
                task.setup_config(schema, daily=daily)
                self.assertEqual(task.CONF_START_DATE in schema.default_config, daily)
                self.assertEqual(task.CONF_DAILY_START_DAY in schema.default_config, daily)
                self.assertTrue(schema.config_type[task.CONF_TARGET]["allow_duplication"])

    def test_daily_task_skips_before_the_configured_weekday(self):
        self.set_run_date(date(2026, 11, 13))
        task = FakePilgrimage()
        task.config[task.CONF_DAILY_START_DAY] = "周日"
        self.assertIsNone(task.do_run())
        task.open_target_list.assert_not_called()

        task.config[task.CONF_DAILY_START_DAY] = "周五"
        self.assertTrue(task.do_run())

    def test_counter_parses_valid_counts_and_rejects_ambiguous_results(self):
        for text, expected in (
            ("0/3", 0),
            ("1 / 3", 1),
            ("剩余次数: ２／３", 2),
            ("3/3", 3),
            ("", None),
            ("4/3", None),
            ("2/30", None),
            ("-1/3", None),
            ("1.2/3", None),
            ("0/3 2/3", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(AnomalyPilgrimage.parse_remaining_attempts(text), expected)

    def test_invalid_rotation_dates_are_rejected(self):
        for value in ("2026-02-30", "2026-11-10"):
            with self.subTest(value=value):
                task = FakePilgrimage()
                task.config[task.CONF_START_DATE] = value
                with self.assertRaises(ValueError):
                    task.rotation_offset(date(2026, 11, 9))

    def test_invalid_target_rejects_the_whole_queue(self):
        task = FakePilgrimage(("A", "missing", "B"))
        with self.assertRaises(ValueError):
            task.resolve_targets()

    def test_queue_keeps_duplicates_and_wraps_in_configured_order(self):
        for targets, expected in (
            (("A", "A", "B"), [call("A"), call("A"), call("B")]),
            (("B", "A"), [call("B"), call("A"), call("B")]),
        ):
            with self.subTest(targets=targets):
                task = FakePilgrimage(targets)
                self.assertTrue(task.do_run())
                self.assertEqual(task.enter_target.call_args_list, expected)
                self.assertEqual(task.config[task.CONF_TARGET], list(targets))

    def test_rotation_uses_elapsed_weeks_and_rewards_already_claimed(self):
        self.set_run_date(date(2026, 11, 9))
        task = FakePilgrimage(("A", "B", "C", "D", "E"), remaining=2)
        task.config[task.CONF_START_DATE] = "2026-11-02"

        self.assertTrue(task.do_run())
        self.assertEqual(task.enter_target.call_args_list, [call("E"), call("A")])

    def test_reward_budget_is_limited_by_stamina_and_remaining_rewards(self):
        for stamina, remaining, expected in ((59, 3, 0), (119, 3, 1), (180, 2, 2), (180, 0, 0)):
            with self.subTest(stamina=stamina, remaining=remaining):
                task = FakePilgrimage(stamina=stamina, remaining=remaining)
                self.assertEqual(task.do_run(), expected > 0)
                self.assertEqual(task.info["完成次数"], expected)
                self.assertEqual(task.info["本周剩余次数"], remaining - expected)

    def test_unconfirmed_reward_stops_the_queue_without_spending_its_budget(self):
        task = FakePilgrimage(("A", "B"))
        task.claim_target_reward.side_effect = [True, False]

        self.assertFalse(task.do_run())
        self.assertEqual(task.info["完成次数"], 1)
        self.assertEqual(task.info["本周剩余次数"], 2)
        self.assertEqual(task.claim_target_reward.call_count, 2)

    def test_wipe_retry_budget_resets_after_a_successful_encounter(self):
        task = FakePilgrimage(remaining=2)
        task.fight_target_once.side_effect = (
            [TeamSurvivalStatus.WIPED] * 3 + [TeamSurvivalStatus.NO_DEATHS]
        ) * 2

        self.assertTrue(task.do_run())
        self.assertEqual(task.info["完成次数"], 2)

    def test_failed_boss_is_skipped_while_other_bosses_can_still_claim_rewards(self):
        task = FakePilgrimage(("A", "B", "A"))
        task.fight_target_once.side_effect = (
            [TeamSurvivalStatus.WIPED] * 7 + [TeamSurvivalStatus.NO_DEATHS]
        )

        self.assertFalse(task.do_run())
        self.assertEqual(task.enter_target.call_args_list, [call("A"), call("B")])
        self.assertEqual(task.info["失败目标"], ["A"])
        self.assertEqual(task.info["完成次数"], 1)

    def test_single_failed_boss_exits_after_three_retries_even_with_rewards_left(self):
        task = FakePilgrimage(remaining=2)
        task.fight_target_once.return_value = TeamSurvivalStatus.WIPED

        self.assertFalse(task.do_run())
        task.enter_target.assert_called_once_with("A")
        task.exit_target.assert_called_once_with()
        self.assertEqual(task.retry_target.call_count, 3)
        self.assertEqual(task.info["本周剩余次数"], 2)

    def test_unconfirmed_rechallenge_aborts_the_encounter(self):
        task = FakePilgrimage()
        task.fight_target_once.return_value = TeamSurvivalStatus.WIPED
        task.retry_target.return_value = False

        with self.assertRaises(CannotFindException):
            task.run_target_combat("A")
        task.fight_target_once.assert_called_once_with()

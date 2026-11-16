import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ok import CannotFindException, TaskDisabledException, WaitFailedException

from src.tasks.AnomalyTask import AnomalyTask


class FakeDailyTask(AnomalyTask):
    def __init__(self, config):
        self.config = config
        self.sync_config = Mock()
        self.log_info = Mock()
        self.log_warning = Mock()


class TestAnomalyTask(unittest.TestCase):
    def _entry_task(self, *, stamina=200, target=180):
        task = FakeDailyTask(
            {
                AnomalyTask.CONF_TASK_TYPE: AnomalyTask.TASK_EXP_COIN,
                AnomalyTask.CONF_EXP_TARGET: AnomalyTask.EXP_CHAR,
            }
        )
        task.info = {}
        task.info_set = task.info.__setitem__
        task.ensure_main = Mock()
        task.open_f1_domain = Mock()
        task.pos = SimpleNamespace(
            panels=SimpleNamespace(f1=SimpleNamespace(domain=SimpleNamespace(exp=(0.1, 0.2))))
        )
        task.operate_click = Mock()
        task.sleep = Mock()

        def read_target():
            task.info_set("must use stamina", target)
            return target

        def read_stamina():
            if stamina >= 0:
                task.info_set("当前体力", stamina)
            return stamina

        task.resolve_stamina_target = Mock(side_effect=read_target)
        task.get_stamina = Mock(side_effect=read_stamina)
        task.wait_until = Mock(return_value=[SimpleNamespace(y=1)])
        task.click_traval_button = Mock()
        task.wait_in_team = Mock()
        task.enter_anomaly_from_interac = Mock()
        return task

    def test_full_entry_restarts_after_failures_at_each_navigation_stage(self):
        for stage in (
            "ensure_main",
            "open_f1_domain",
            "wait_until",
            "click_traval_button",
            "wait_in_team",
            "enter_anomaly_from_interac",
        ):
            with self.subTest(stage=stage):
                task = self._entry_task()
                operation = getattr(task, stage)
                operation.side_effect = [
                    CannotFindException(),
                    WaitFailedException(),
                    CannotFindException(),
                    operation.return_value,
                ]

                self.assertTrue(task.enter_anomaly(0))

                self.assertEqual(task.ensure_main.call_count, 4)
                self.assertEqual(operation.call_count, 4)
                task.get_stamina.assert_called_once_with()
                task.resolve_stamina_target.assert_called_once_with()
                self.assertEqual(task.info_get("双倍次数"), 2)
                self.assertEqual(task.info_get("单倍次数"), 1)

    def test_full_entry_stops_after_four_attempts(self):
        task = self._entry_task()
        task.enter_anomaly_from_interac.side_effect = CannotFindException()

        self.assertFalse(task.enter_anomaly(0))

        self.assertEqual(task.open_f1_domain.call_count, 4)
        self.assertEqual(task.enter_anomaly_from_interac.call_count, 4)
        task.get_stamina.assert_called_once_with()

    def test_nearby_reentry_success_does_not_open_full_navigation(self):
        task = self._entry_task()

        self.assertTrue(task.enter_anomaly(2, retry=True))

        task.enter_anomaly_from_interac.assert_called_once_with(2, retry=True)
        task.open_f1_domain.assert_not_called()

    def test_reentry_fallback_preserves_reward_plan_and_completed_progress(self):
        task = self._entry_task()
        task.enter_anomaly_from_interac.side_effect = [None, CannotFindException(), None]
        task.do_combat_and_claim = Mock(side_effect=[True, False, True, True])
        task.exit_anomaly = Mock()
        task.wait_click_confirm = Mock()

        self.assertTrue(task.do_run())

        self.assertEqual(task.open_f1_domain.call_count, 2)
        task.get_stamina.assert_called_once_with()
        task.resolve_stamina_target.assert_called_once_with()
        self.assertEqual(
            [call.args[0] for call in task.do_combat_and_claim.call_args_list],
            [True, True, True, False],
        )
        self.assertEqual(task.enter_anomaly_from_interac.call_args_list[1].kwargs, {"retry": True})

    def test_failed_reentry_stops_combat(self):
        task = self._entry_task()
        task.enter_anomaly_from_interac.side_effect = [None] + [WaitFailedException()] * 5
        task.do_combat_and_claim = Mock(return_value=False)
        task.exit_anomaly = Mock()
        task.wait_click_confirm = Mock()

        self.assertFalse(task.do_run())

        self.assertEqual(task.open_f1_domain.call_count, 5)
        task.do_combat_and_claim.assert_called_once_with(True)
        task.wait_click_confirm.assert_not_called()

    def test_skip_and_insufficient_stamina_do_not_retry(self):
        for target, stamina, expected in ((0, 200, None), (180, 0, False)):
            with self.subTest(target=target, stamina=stamina):
                task = self._entry_task(target=target, stamina=stamina)

                self.assertIs(task.do_run(), expected)

                task.ensure_main.assert_called_once()
                task.enter_anomaly_from_interac.assert_not_called()

    def test_failed_stamina_read_is_retried(self):
        task = self._entry_task(stamina=-1)

        self.assertFalse(task.enter_anomaly(0))

        self.assertEqual(task.get_stamina.call_count, 4)
        self.assertNotIn("当前体力", task.info)
        task.enter_anomaly_from_interac.assert_not_called()

    def test_reward_plan_respects_stamina_target_and_reward_limit(self):
        for stamina, target, limit, expected in (
            (200, None, 0, (2, 1)),
            (80, 180, 0, (1, 0)),
            (200, 41, 0, (1, 0)),
            (200, 180, 1, (0, 1)),
        ):
            with self.subTest(stamina=stamina, target=target, limit=limit):
                task = self._entry_task(stamina=stamina, target=target)
                task.config[task.CONF_CLAIM_REWARD_COUNT] = limit

                self.assertTrue(task.enter_anomaly(0))

                self.assertEqual((task.info_get("双倍次数"), task.info_get("单倍次数")), expected)

    def test_new_run_reads_stamina_again_after_framework_clears_info(self):
        task = self._entry_task()
        self.assertTrue(task.enter_anomaly(0))

        task.info_clear()

        self.assertTrue(task.enter_anomaly(0))
        self.assertEqual(task.get_stamina.call_count, 2)
        self.assertEqual(task.resolve_stamina_target.call_count, 2)

    def test_cancellation_and_unexpected_errors_propagate_without_retry(self):
        for retry in (False, True):
            for error in (TaskDisabledException(), RuntimeError("unexpected")):
                with self.subTest(retry=retry, error=type(error)):
                    task = self._entry_task()
                    task.enter_anomaly_from_interac.side_effect = error

                    with self.assertRaises(type(error)):
                        task.enter_anomaly(0, retry=retry)

                    task.enter_anomaly_from_interac.assert_called_once()

    def test_interaction_wait_failures_fall_back_to_full_entry(self):
        for failed_wait in range(4):
            with self.subTest(failed_wait=failed_wait):
                task = self._entry_task()
                task.enter_anomaly_from_interac = AnomalyTask.enter_anomaly_from_interac.__get__(
                    task
                )
                task.enter_anomaly_full = Mock(return_value=True)
                task.walk_until_interac = Mock()
                task.click_sub_idx = Mock()
                task.wait_until.side_effect = [True] * failed_wait + [WaitFailedException()]

                self.assertTrue(task.enter_anomaly(0, retry=True))

                task.enter_anomaly_full.assert_called_once_with(0)
                task.wait_in_team.assert_not_called()
                for call in task.wait_until.call_args_list:
                    self.assertTrue(call.kwargs["raise_if_not_found"])

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

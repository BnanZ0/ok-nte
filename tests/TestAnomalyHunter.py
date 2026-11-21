import unittest
from unittest.mock import Mock, patch

from src.combat.BaseCombatTask import TeamSurvivalStatus
from src.tasks.AnomalyHunter import AnomalyHunter
from src.tasks.BaseStaminaTask import BaseStaminaTask


class TestAnomalyHunterClaimFallback(unittest.TestCase):
    def _make_hunter(self, *, keep_reward_dungeon):
        hunter = object.__new__(AnomalyHunter)
        hunter.config = {}
        if keep_reward_dungeon is not None:
            hunter.config[hunter.CONF_KEEP_DUNGEON] = keep_reward_dungeon
        hunter.info_set = Mock()
        hunter.log_warning = Mock()
        hunter.log_info = Mock()
        hunter.ensure_main = Mock()
        hunter.resolve_stamina_target = Mock(return_value=None)
        hunter.open_hunter_page = Mock()
        hunter.sleep = Mock()
        hunter.get_stamina = Mock(return_value=hunter.TASK_COST)
        hunter.start_hunter_attempt = Mock()
        hunter.wait_in_team = Mock()
        hunter.rotate_and_find_treasure = Mock(return_value=True)
        hunter.walk_until_combat = Mock(return_value=True)
        hunter.combat_once = Mock(return_value=TeamSurvivalStatus.NO_DEATHS)
        hunter.walk_to_boss_treasure = Mock(return_value=True)
        hunter.wait_until = Mock(return_value=True)
        hunter.operate_click = Mock()
        hunter.exit_anomaly = Mock()
        hunter.click_nearest_map_teleport = Mock()
        return hunter

    def _run_hunter(self, hunter):
        with patch.object(BaseStaminaTask, "run"):
            hunter.run()

    def test_claim_failure_retries_when_keep_is_disabled_or_absent(self):
        for keep_reward_dungeon in (False, None):
            with self.subTest(keep_reward_dungeon=keep_reward_dungeon):
                hunter = self._make_hunter(keep_reward_dungeon=keep_reward_dungeon)
                hunter.walk_to_boss_treasure.side_effect = [False, True]

                self.assertTrue(hunter.do_run())

                self.assertEqual(hunter.start_hunter_attempt.call_count, 2)
                hunter.click_nearest_map_teleport.assert_called_once()

    def test_claim_failure_keeps_dungeon_when_enabled(self):
        for leftover_treasure in (True, False):
            with self.subTest(leftover_treasure=leftover_treasure):
                hunter = self._make_hunter(keep_reward_dungeon=True)
                hunter.rotate_and_find_treasure.return_value = leftover_treasure
                hunter.walk_to_boss_treasure.return_value = not leftover_treasure
                hunter.combat_once.return_value = TeamSurvivalStatus.DEAD
                hunter.wait_until.return_value = False

                with self.assertRaisesRegex(Exception, "手动领取"):
                    self._run_hunter(hunter)

                self.assertEqual(hunter.combat_once.call_count, 0 if leftover_treasure else 1)
                hunter.start_hunter_attempt.assert_called_once()
                hunter.exit_anomaly.assert_not_called()
                hunter.click_nearest_map_teleport.assert_not_called()

    def test_wipe_retries_even_when_enabled(self):
        hunter = self._make_hunter(keep_reward_dungeon=True)
        hunter.rotate_and_find_treasure.return_value = False
        hunter.combat_once.side_effect = [TeamSurvivalStatus.WIPED, TeamSurvivalStatus.NO_DEATHS]

        self.assertTrue(hunter.do_run())

        self.assertEqual(hunter.start_hunter_attempt.call_count, 2)
        hunter.click_nearest_map_teleport.assert_called_once()

    def test_unexpected_error_exits_and_propagates_when_enabled(self):
        hunter = object.__new__(AnomalyHunter)
        hunter.config = {hunter.CONF_KEEP_DUNGEON: True}
        error = RuntimeError("unexpected error")
        hunter.do_run = Mock(side_effect=error)
        hunter.exit_anomaly = Mock()

        with self.assertRaises(RuntimeError) as raised:
            self._run_hunter(hunter)

        self.assertIs(raised.exception, error)
        hunter.exit_anomaly.assert_called_once()

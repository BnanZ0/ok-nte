import unittest
from unittest.mock import Mock

from src.combat.BaseCombatTask import TeamSurvivalStatus
from src.tasks.AnomalyHunter import AnomalyHunter


class TestAnomalyHunterClaimFallback(unittest.TestCase):
    """覆盖"领奖失败保留副本"开关的判定分支。"""

    def _make_hunter(self, keep_reward_dungeon):
        hunter = object.__new__(AnomalyHunter)
        hunter._keep_reward_dungeon = keep_reward_dungeon
        hunter.claim_failure_pending = False
        hunter.log_warning = Mock()
        hunter.log_info = Mock()
        return hunter

    def test_default_disabled_does_not_keep_dungeon(self):
        hunter = self._make_hunter(keep_reward_dungeon=False)

        self.assertFalse(hunter._handle_claim_failure(reward_possible=True))
        self.assertFalse(hunter.claim_failure_pending)
        hunter.log_warning.assert_called_once_with("未能领取BOSS宝箱奖励")

    def test_enabled_keeps_dungeon_after_battle(self):
        hunter = self._make_hunter(keep_reward_dungeon=True)

        self.assertFalse(hunter._handle_claim_failure(reward_possible=True))
        self.assertTrue(hunter.claim_failure_pending)
        hunter.log_warning.assert_called_once()
        self.assertTrue(hunter.log_warning.call_args.kwargs["notify"])

    def test_enabled_keeps_dungeon_for_leftover_treasure(self):
        """开局就发现遗留宝箱也算有奖励可领, 领奖失败时应能保留副本。

        必须走真实的 do_combat_and_claim(), 否则把遗留宝箱分支改回 False 时
        该用例仍然会通过, 失去回归价值。
        """
        hunter = self._make_hunter(keep_reward_dungeon=True)
        hunter.rotate_and_find_treasure = Mock(return_value=True)
        hunter.walk_to_boss_treasure = Mock(return_value=False)
        hunter.walk_until_combat = Mock()
        hunter.combat_once = Mock()

        self.assertFalse(hunter.do_combat_and_claim())
        self.assertTrue(hunter.claim_failure_pending)
        hunter.combat_once.assert_not_called()

    def test_wipe_never_keeps_dungeon(self):
        hunter = self._make_hunter(keep_reward_dungeon=True)

        self.assertFalse(hunter._handle_claim_failure(reward_possible=False))
        self.assertFalse(hunter.claim_failure_pending)
        hunter.log_warning.assert_called_once_with("未能领取BOSS宝箱奖励")

    def test_daily_chain_never_keeps_dungeon(self):
        """日常链直接调用 do_run(), 不经过 run(), _keep_reward_dungeon 恒为 False。"""
        hunter = self._make_hunter(keep_reward_dungeon=False)

        self.assertFalse(hunter._handle_claim_failure(reward_possible=True))
        self.assertFalse(hunter.claim_failure_pending)

    def test_combat_done_only_excludes_wipe(self):
        """非团灭结果都视为可能有奖励, 不使用其它未验证的完成信号。"""
        hunter = self._make_hunter(keep_reward_dungeon=True)
        hunter.rotate_and_find_treasure = Mock(return_value=False)
        hunter.walk_until_combat = Mock(return_value=True)
        hunter.log_info = Mock()
        hunter.snapshot = {}

        for status in (TeamSurvivalStatus.NO_DEATHS, TeamSurvivalStatus.DEAD):
            hunter.combat_once = Mock(return_value=status)
            hunter.walk_to_boss_treasure = Mock(return_value=False)
            hunter.claim_failure_pending = False
            hunter._handle_claim_failure = Mock(wraps=hunter._handle_claim_failure)
            hunter.do_combat_and_claim()
            self.assertEqual(
                hunter._handle_claim_failure.call_args.args[0],
                True,
                f"{status} 应视为可能有奖励",
            )

        hunter.claim_failure_pending = False
        hunter.combat_once = Mock(return_value=TeamSurvivalStatus.WIPED)
        hunter.walk_to_boss_treasure = Mock(return_value=False)
        hunter.do_combat_and_claim()
        self.assertFalse(hunter.claim_failure_pending)


if __name__ == "__main__":
    unittest.main()

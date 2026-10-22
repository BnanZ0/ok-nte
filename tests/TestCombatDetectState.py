import unittest
from unittest.mock import Mock, patch

from src.combat.CombatCheck import (
    CombatCheck,
    CombatDetectPhase,
    CombatDetectPolicy,
    CombatDetectResult,
    CombatDetectState,
)
from src.utils import clock


class TestCombatDetectState(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(CombatCheck)
        self.task.combat_detect_policy = CombatDetectPolicy(miss_required=3, uncertain_seconds=2)
        self.task.combat_detect_state = CombatDetectState()
        self.task.log_info = Mock()
        self.task.middle_click = Mock()

    @patch("src.utils.clock._read_monotonic", side_effect=[10, 11, 12])
    def test_enters_uncertain_on_configured_miss_count(self, _time):
        miss = CombatDetectResult(False, "miss")

        self.assertIs(
            self.task._update_combat_detect_state(miss), CombatDetectPhase.IN_COMBAT
        )
        self.assertIs(
            self.task._update_combat_detect_state(miss), CombatDetectPhase.IN_COMBAT
        )
        self.assertIs(
            self.task._update_combat_detect_state(miss), CombatDetectPhase.UNCERTAIN
        )
        self.assertEqual(self.task.combat_detect_state.uncertain_until, clock.TimePoint(14))
        self.task.middle_click.assert_called_once_with()

    @patch("src.utils.clock._read_monotonic", return_value=11)
    def test_detection_hit_leaves_uncertain_before_timeout(self, _time):
        self.task.combat_detect_state.uncertain_until = clock.TimePoint(12)

        phase = self.task._update_combat_detect_state(CombatDetectResult(True, "target"))

        self.assertIs(phase, CombatDetectPhase.IN_COMBAT)
        self.assertFalse(self.task.combat_detect_uncertain)

    @patch("src.utils.clock._read_monotonic", return_value=12)
    def test_uncertain_timeout_moves_to_final_retarget_for_pending_detection(self, _time):
        self.task.combat_detect_state.uncertain_until = clock.TimePoint(12)

        phase = self.task._update_combat_detect_state(CombatDetectResult(None, "pending"))

        self.assertIs(phase, CombatDetectPhase.VERIFY_TARGET)


if __name__ == "__main__":
    unittest.main()

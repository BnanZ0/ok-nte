import unittest
from unittest.mock import Mock

from src.char.BaseChar import BaseChar
from src.combat.BaseCombatTask import BaseCombatTask, SleepCheckSkip
from src.tasks.DSDFarmTask import DSDFarmTask


class TestCombatSession(unittest.TestCase):
    def _task(self):
        task = BaseCombatTask.__new__(BaseCombatTask)
        task.sleep_check_skip = SleepCheckSkip()
        task.combat_session = None
        task.current_char = object()
        task.start_char = object()
        task.switch_calls = 0

        def switch_to_start():
            task.switch_calls += 1
            task.current_char = task.start_char

        task.switch_to_combat_start_char = switch_to_start
        task.get_current_char = lambda raise_exception=False: task.current_char
        task.click = lambda *_args, **_kwargs: None
        return task

    def test_begin_combat_session_switches_once_and_records_start_char(self):
        task = self._task()

        session = task.begin_combat_session()
        duplicate = task.begin_combat_session()

        self.assertIs(session, duplicate)
        self.assertEqual(task.switch_calls, 1)
        self.assertIs(session.start_char, task.start_char)
        self.assertGreater(session.combat_start, 0)
        self.assertFalse(task.sleep_check_skip.all)

    def test_first_engage_is_recorded_once_for_the_active_session(self):
        task = self._task()
        task.begin_combat_session()
        first = object()
        later = object()

        task.record_first_engage(first)
        task.record_first_engage(later)

        self.assertTrue(task.is_first_engage(first))
        self.assertFalse(task.is_first_engage(later))

    def test_first_engage_can_only_be_consumed_once(self):
        task = self._task()
        task.begin_combat_session()
        first = object()
        later = object()
        task.record_first_engage(first)

        self.assertTrue(task.consume_first_engage(first))
        self.assertFalse(task.consume_first_engage(first))
        self.assertFalse(task.consume_first_engage(later))
        self.assertTrue(task.is_first_engage(first))

    def test_base_char_delegates_first_engage_to_task_session(self):
        task = self._task()
        task.begin_combat_session()
        char = BaseChar.__new__(BaseChar)
        char.task = task
        task.record_first_engage(char)

        self.assertTrue(char.is_first_engage())
        self.assertTrue(char.consume_first_engage())
        self.assertFalse(char.consume_first_engage())

    def test_base_char_perform_records_first_engage_before_actions(self):
        task = Mock()
        char = BaseChar(task, index=0)
        char.planner_handles_arc = True
        char.switch_next_char = Mock()

        char.perform()

        task.record_first_engage.assert_called_once_with(char)
        task.combat_planner.perform_current_char.assert_called_once_with(char)

    def test_dsd_switching_config_can_be_set_before_combat_starts(self):
        task = DSDFarmTask.__new__(DSDFarmTask)
        task.sleep_check_skip = SleepCheckSkip()
        task.config = {task.CONF_DONT_SWITCH: True}
        task.combat_session = None
        task.current_char = object()
        task.switch_to_combat_start_char = lambda: None
        task.get_current_char = lambda raise_exception=False: task.current_char
        task.click = lambda *_args, **_kwargs: None

        task.combat_session.switch_enabled = not task.config.get(task.CONF_DONT_SWITCH, False)
        session = task.begin_combat_session()
        self.assertFalse(session.switch_enabled)
        task.config[task.CONF_DONT_SWITCH] = False
        self.assertFalse(task.begin_combat_session().switch_enabled)

    def test_ultimate_usage_is_a_combat_session_policy(self):
        task = self._task()

        task.combat_session.use_ultimate = False

        self.assertFalse(task.begin_combat_session().use_ultimate)


if __name__ == "__main__":
    unittest.main()

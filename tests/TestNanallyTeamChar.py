import unittest

from ok.test.TaskTestCase import TaskTestCase

from src.char.CharFactory import get_char_by_name
from src.char.NanallyTeamJiuyuan import NanallyTeamJiuyuan
from src.char.NanallyTeamNanally import NanallyTeamNanally
from src.char.NanallyTeamSakiri import NanallyTeamSakiri
from src.char.NanallyTeamZero import NanallyTeamZero
from src.char.custom.BuiltinComboRegistry import BuiltinComboRegistry
from src.config import config
from src.tasks.trigger.AutoCombatTask import AutoCombatTask


class TestNanallyTeamChar(TaskTestCase):
    task_class = AutoCombatTask
    config = config

    def test_builtin_team_chars_registered(self):
        cases = [
            ("char_nanally_team_nanally", NanallyTeamNanally),
            ("char_nanally_team_zero", NanallyTeamZero),
            ("char_nanally_team_jiuyuan", NanallyTeamJiuyuan),
            ("char_nanally_team_sakiri", NanallyTeamSakiri),
        ]

        for builtin_key, char_cls in cases:
            combo_ref = BuiltinComboRegistry.make_ref(builtin_key)
            char = get_char_by_name(self.task, 0, "test_char", combo_ref=combo_ref)
            self.assertIsInstance(char, char_cls)


if __name__ == "__main__":
    unittest.main()

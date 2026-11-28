# -*- coding: utf-8 -*-
"""日常子项注册钩子的离线测试。"""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from src.tasks.cultivation_goal.daily_hook import TASK_ID, register_daily_routine_entry


@dataclass(frozen=True)
class FakeEntry:
    task_id: str
    task_class: type
    enabled_by_default: bool = False
    exclusive_group: str | None = None
    daily_config: bool = False


class FakeCultivationGoal:
    pass


def make_host(*entries):
    return SimpleNamespace(DailyRoutineEntry=FakeEntry, DAILY_ROUTINE_ENTRIES=tuple(entries))


class DailyHookTests(unittest.TestCase):
    def test_registers_as_first_item(self):
        host = make_host(FakeEntry("coffee", object), FakeEntry("fountain", object))
        self.assertTrue(register_daily_routine_entry(FakeCultivationGoal, module=host))
        ids = [entry.task_id for entry in host.DAILY_ROUTINE_ENTRIES]
        self.assertEqual(ids[0], TASK_ID)
        self.assertEqual(ids[1:], ["coffee", "fountain"])
        entry = host.DAILY_ROUTINE_ENTRIES[0]
        self.assertIs(entry.task_class, FakeCultivationGoal)
        self.assertTrue(entry.enabled_by_default)
        self.assertTrue(entry.daily_config)

    def test_idempotent_and_refreshes_class(self):
        host = make_host(FakeEntry("coffee", object))
        register_daily_routine_entry(FakeCultivationGoal, module=host)
        other = type("CultivationGoalTask", (), {})
        register_daily_routine_entry(other, module=host)
        hits = [e for e in host.DAILY_ROUTINE_ENTRIES if e.task_id == TASK_ID]
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0].task_class, other)

    def test_missing_host_module_is_not_fatal(self):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("宿主结构已变")

        self.assertFalse(register_daily_routine_entry(FakeCultivationGoal, module=Boom()))

    def test_import_failure_is_not_fatal(self):
        import src.tasks.cultivation_goal.daily_hook as hook

        original = hook.importlib.import_module
        hook.importlib.import_module = lambda name: (_ for _ in ()).throw(ImportError(name))
        try:
            self.assertFalse(register_daily_routine_entry(FakeCultivationGoal))
        finally:
            hook.importlib.import_module = original


if __name__ == "__main__":
    unittest.main()

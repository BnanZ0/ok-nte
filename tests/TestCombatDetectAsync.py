import unittest
from concurrent.futures import CancelledError

from src.combat.CombatCheck import CombatCheck


class TestableCombatCheck(CombatCheck):
    @property
    def thread_pool_executor(self):
        return self.fake_executor


class FakeFuture:
    def __init__(self, result=False):
        self.callback = None
        self.cancelled_flag = False
        self.result_value = result

    def add_done_callback(self, callback):
        self.callback = callback

    def cancel(self):
        self.cancelled_flag = True
        if self.callback is not None:
            self.callback(self)
        return True

    def cancelled(self):
        return self.cancelled_flag

    def running(self):
        return False

    def done(self):
        return self.cancelled_flag

    def result(self):
        if self.cancelled_flag:
            raise CancelledError()
        return self.result_value


class FakeExecutor:
    def __init__(self, futures):
        self.futures = list(futures)

    def submit(self, *args, **kwargs):
        return self.futures.pop(0)


class TestCombatDetectAsync(unittest.TestCase):
    def test_forced_lv_refresh_ignores_cancelled_old_future(self):
        first = FakeFuture()
        second = FakeFuture()
        task = object.__new__(TestableCombatCheck)
        task._lv_async = True
        task.find_lv_future = None
        task._find_lv_latency = 0
        task._find_lv_async_started_at = None
        task.fake_executor = FakeExecutor([first, second])

        TestableCombatCheck.find_lv_async(task, frame="frame")
        ret = TestableCombatCheck.find_lv_async(task, frame="frame", force=True)

        self.assertTrue(ret)
        self.assertTrue(first.cancelled())
        self.assertIs(task.find_lv_future, second)
        self.assertTrue(task._lv_async)


if __name__ == "__main__":
    unittest.main()

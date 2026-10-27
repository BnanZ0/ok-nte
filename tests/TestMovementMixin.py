import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from ok import TaskDisabledException

from src.tasks.mixin.MovementMixin import LATERAL_DIRECTION_CHANGE_WINDOW, MovementMixin
from src.utils import clock


class _MovementTask(MovementMixin):
    def __init__(self):
        self.elapsed = 0.0
        self.held_keys = set()
        self.key_events = []
        self.searches = []
        self.search_result = None
        self.search_duration = 0
        self.on_search = None
        self.recovered = False

    def next_frame(self):
        self.elapsed += 0.25

    def sleep(self, seconds):
        self.elapsed += seconds

    def send_key_down(self, key):
        self.held_keys.add(key)
        self.key_events.append(("down", key))

    def send_key_up(self, key):
        self.held_keys.discard(key)
        self.key_events.append(("up", key))

    def send_key(self, key, after_sleep=0):
        self.sleep(after_sleep)

    def middle_click(self, after_sleep=0):
        self.sleep(after_sleep)

    def width_of_screen(self, fraction):
        return 1000 * fraction

    def height_of_screen(self, fraction):
        return 1000 * fraction

    def log_info(self, message):
        pass

    def rotate_and_find(self, find, interrupt):
        self.searches.append((self.elapsed, set(self.held_keys)))
        self.sleep(self.search_duration)
        if self.on_search:
            return self.on_search(find, interrupt)
        self.recovered = bool(self.search_result)
        return self.search_result


class TestMovementMixin(unittest.TestCase):
    def _walker(self):
        task = _MovementTask()
        self.enterContext(patch.object(clock, "_read_monotonic", lambda: task.elapsed))
        return task

    def test_frequent_lateral_direction_triggers_after_four_fast_lateral_decisions(self):
        changes = deque()

        self.assertFalse(MovementMixin._has_frequent_lateral_direction("a", changes, now=0.0))
        self.assertFalse(MovementMixin._has_frequent_lateral_direction("d", changes, now=0.1))
        self.assertFalse(MovementMixin._has_frequent_lateral_direction("s", changes, now=0.2))
        self.assertFalse(MovementMixin._has_frequent_lateral_direction("a", changes, now=0.3))
        self.assertTrue(MovementMixin._has_frequent_lateral_direction("d", changes, now=0.4))

    def test_frequent_lateral_direction_expires_but_keeps_counts_across_vertical_moves(self):
        changes = deque([0.0, 0.1, 0.2])
        after_window = LATERAL_DIRECTION_CHANGE_WINDOW + 0.5

        self.assertFalse(
            MovementMixin._has_frequent_lateral_direction("a", changes, now=after_window)
        )
        self.assertEqual(deque([after_window]), changes)

        self.assertFalse(
            MovementMixin._has_frequent_lateral_direction("s", changes, now=after_window + 0.1)
        )
        self.assertEqual(deque([after_window]), changes)

    def test_short_target_losses_reset_after_each_detection_without_rotating(self):
        task = self._walker()
        target = SimpleNamespace(center=lambda: (500, 300))

        def find():
            missing = 0.5 <= task.elapsed < 1.25 or 1.5 <= task.elapsed < 2.25
            return None if missing else target

        self.assertTrue(
            task._walk_to_box_loop(find, time_out=3, end_condition=lambda: task.elapsed >= 2.5)
        )
        self.assertEqual(task.searches, [])
        self.assertEqual(task.held_keys, set())

    def test_target_lost_for_one_second_stops_and_searches_before_resuming(self):
        task = self._walker()
        initial = SimpleNamespace(center=lambda: (500, 300))
        recovered = SimpleNamespace(center=lambda: (900, 300))
        task.search_result = [recovered]
        task.search_duration = 5

        def find():
            if task.recovered:
                return [recovered]
            return initial if task.elapsed < 0.5 else None

        self.assertTrue(
            task._walk_to_box_loop(
                find,
                time_out=2,
                end_condition=lambda: task.recovered and "d" in task.held_keys,
            )
        )
        self.assertEqual(len(task.searches), 1)
        searched_at, held_during_search = task.searches[0]
        self.assertGreaterEqual(searched_at, 1.5)
        self.assertEqual(held_during_search, set())
        self.assertIn(("down", "w"), task.key_events)
        self.assertIn(("down", "d"), task.key_events)
        self.assertGreater(task.elapsed, 5)
        self.assertEqual(task.held_keys, set())

    def test_failed_recovery_returns_false_without_resuming_stale_direction(self):
        task = self._walker()
        target = SimpleNamespace(center=lambda: (500, 300))
        self.assertFalse(
            task._walk_to_box_loop(lambda: target if task.elapsed < 0.5 else None, time_out=3)
        )
        self.assertEqual(len(task.searches), 1)
        self.assertEqual(task.key_events, [("down", "w"), ("up", "w")])
        self.assertEqual(task.held_keys, set())

    def test_initial_loss_waits_one_second_without_guessing_a_direction(self):
        task = self._walker()
        self.assertFalse(task._walk_to_box_loop(lambda: None, time_out=3))
        self.assertEqual(len(task.searches), 1)
        self.assertGreaterEqual(task.searches[0][0], 1.25)
        self.assertEqual(task.key_events, [])

    def test_end_condition_during_search_finishes_successfully(self):
        task = self._walker()
        finished = False

        def search(find, interrupt):
            nonlocal finished
            finished = True
            self.assertTrue(interrupt())
            return None

        task.on_search = search
        self.assertTrue(
            task._walk_to_box_loop(lambda: None, time_out=3, end_condition=lambda: finished)
        )
        self.assertEqual(task.held_keys, set())

    def test_stopping_during_recovery_propagates_with_movement_keys_released(self):
        task = self._walker()
        target = SimpleNamespace(center=lambda: (500, 300))

        def stop(find, interrupt):
            raise TaskDisabledException("stopped during recovery")

        task.on_search = stop
        with self.assertRaises(TaskDisabledException):
            task._walk_to_box_loop(lambda: target if task.elapsed < 0.5 else None, time_out=3)
        self.assertEqual(task.held_keys, set())


if __name__ == "__main__":
    unittest.main()

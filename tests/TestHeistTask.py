import unittest

import win32con

from src.tasks.trigger.HeistTask import HeistTask
from src.utils import clock


class _KeyboardEvent:
    def __init__(self, vk_code):
        self.flags = 0
        self.vkCode = vk_code


class _Listener:
    def __init__(self):
        self.suppressed_event_count = 0

    def suppress_event(self):
        self.suppressed_event_count += 1


class TestHeistTask(unittest.TestCase):
    def test_get_vk_codes_always_returns_three_slots(self):
        task = object.__new__(HeistTask)

        vk_codes = [
            task._get_vk_codes(None),
            task._get_vk_codes("shift"),
            task._get_vk_codes("lshift"),
            task._get_vk_codes("f1"),
            task._get_vk_codes("f"),
            task._get_vk_codes("invalid"),
        ]

        self.assertTrue(all(len(codes) == HeistTask.VK_CODE_SLOT_COUNT for codes in vk_codes))
        self.assertEqual(task._get_valid_vk_codes("shift"), HeistTask.KEY_MAP["shift"])
        self.assertEqual(task._get_valid_vk_codes("f1"), (win32con.VK_F1,))

    def test_shift_release_immediately_ends_quick_run_interception(self):
        task = object.__new__(HeistTask)
        task.physical_keys_pressed = {win32con.VK_LSHIFT}
        task.suppressed_keys = {win32con.VK_LSHIFT}
        task.listener = _Listener()
        task._is_active = lambda: True
        task._log_target_key_event = lambda *args: None
        task._pick_key_pressed = False
        task._shift_pressed = True
        task._shift_down_time = clock.TimePoint(1)
        task._quick_running = True
        task._quick_run_index = 1
        task._quick_run_time = clock.TimePoint(1)
        task._quick_run_step = 1

        self.assertFalse(task._win32_filter(win32con.WM_KEYUP, _KeyboardEvent(win32con.VK_LSHIFT)))

        self.assertEqual(task.physical_keys_pressed, set())
        self.assertNotIn(win32con.VK_LSHIFT, task.suppressed_keys)
        self.assertFalse(task._quick_running)
        self.assertEqual(task.listener.suppressed_event_count, 1)

        self.assertTrue(task._win32_filter(win32con.WM_KEYDOWN, _KeyboardEvent(win32con.VK_LSHIFT)))

        self.assertEqual(task.listener.suppressed_event_count, 1)

    def test_pick_key_is_suppressed_until_release(self):
        task = object.__new__(HeistTask)
        pick_key = ord("F")
        task.physical_keys_pressed = {pick_key}
        task.suppressed_keys = set()
        task._is_active = lambda: True
        task._suppressed_trigger_keys = lambda: {pick_key}

        self.assertTrue(task._should_suppress(win32con.WM_KEYDOWN, pick_key))
        self.assertTrue(task._should_suppress(win32con.WM_KEYUP, pick_key))
        self.assertNotIn(pick_key, task.suppressed_keys)


if __name__ == "__main__":
    unittest.main()

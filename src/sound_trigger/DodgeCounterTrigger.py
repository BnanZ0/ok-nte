import ctypes
import random
import threading
import time
from typing import Callable, Optional

from ctypes import wintypes
from ok import Logger

logger = Logger.get_logger(__name__)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MAPVK_VK_TO_VSC = 0

LEFT_SHIFT_KEY_CODE = 0xA0

user32 = ctypes.WinDLL('user32', use_last_error=True)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM))


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM))

    def __init__(self, *args, **kwds):
        super(KEYBDINPUT, self).__init__(*args, **kwds)
        if not self.dwFlags & KEYEVENTF_UNICODE:
            self.wScan = user32.MapVirtualKeyExW(self.wVk,
                                                 MAPVK_VK_TO_VSC, 0)


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),
                    ("mi", MOUSEINPUT))

    _anonymous_ = ("_input",)
    _fields_ = (("type", wintypes.DWORD),
                ("_input", _INPUT))


class DodgeCounterTrigger:
    def __init__(
        self,
        task,
        execute_action: Optional[Callable] = None,
        counter_execute_action: Optional[Callable] = None,
    ):
        self.task = task
        self.execute_action = execute_action or self._default_dodge_action
        self.counter_execute_action = counter_execute_action or self._default_counter_action

        self._is_executing = False
        self._execute_lock = threading.Lock()
        self._last_dodge_time = 0.0
        self._last_counter_time = 0.0
        self._min_dodge_interval = 0.5
        self._min_counter_interval = 1.0

    @staticmethod
    def press_key(hexKeyCode):
        x = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=hexKeyCode))
        user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))

    @staticmethod
    def release_key(hexKeyCode):
        x = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=hexKeyCode, dwFlags=KEYEVENTF_KEYUP))
        user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))

    @staticmethod
    def mouse_action(flags):
        x = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(INPUT))

    def execute_dodge(self):
        now = time.time()
        if now - self._last_dodge_time < self._min_dodge_interval:
            logger.debug(f"Dodge skipped, too soon: {now - self._last_dodge_time:.3f}s")
            return

        with self._execute_lock:
            if self._is_executing:
                return
            self._is_executing = True

        try:
            logger.info("Executing dodge")
            self.execute_action()
            self._last_dodge_time = now
            logger.info(f"Dodge executed successfully at {now:.3f}")
        except Exception as e:
            logger.error(f"Dodge execution error: {e}", exc_info=True)
        finally:
            self._is_executing = False

    def execute_counter_attack(self):
        now = time.time()
        if now - self._last_counter_time < self._min_counter_interval:
            return

        with self._execute_lock:
            if self._is_executing:
                return
            self._is_executing = True

        try:
            logger.info("Executing counter attack")
            self.counter_execute_action()
            self._last_counter_time = now
            logger.info(f"Counter attack executed successfully at {now:.3f}")
        except Exception as e:
            logger.error(f"Counter execution error: {e}", exc_info=True)
        finally:
            self._is_executing = False

    def _default_dodge_action(self):
        from src.sound_trigger.SoundCombatContext import SoundCombatContext
        SoundCombatContext.enter_priority()
        try:
            logger.info("Dodge sequence: Right mouse + Left Shift")
            self.mouse_action(MOUSEEVENTF_RIGHTDOWN)
            time.sleep(0.1 + random.random() * 0.2)
            self.mouse_action(MOUSEEVENTF_RIGHTUP)
            time.sleep(0.1)
            self.press_key(LEFT_SHIFT_KEY_CODE)
            time.sleep(0.1 + random.random() * 0.1)
            self.release_key(LEFT_SHIFT_KEY_CODE)
            self.task.next_frame()
        finally:
            SoundCombatContext.exit_priority()

    def _default_counter_action(self):
        from src.sound_trigger.SoundCombatContext import SoundCombatContext
        SoundCombatContext.enter_priority()
        try:
            logger.info("Counter attack sequence: Number key + Left mouse")
            keys = [0x31, 0x32, 0x33, 0x34]
            selected_key = random.choice(keys)
            logger.info(f"Selected number key: {selected_key:#x}")
            self.press_key(selected_key)
            time.sleep(0.05)
            self.release_key(selected_key)
            time.sleep(0.05)
            self.mouse_action(MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.05)
            self.mouse_action(MOUSEEVENTF_LEFTUP)
            self.task.next_frame()
        finally:
            SoundCombatContext.exit_priority()

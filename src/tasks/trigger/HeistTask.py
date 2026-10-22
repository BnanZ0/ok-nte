import time

import win32api
import win32con
from ok import Logger, TriggerTask
from pynput import keyboard
from pynput.keyboard import Controller, Key

from src.tasks.BaseNTETask import BaseNTETask

logger = Logger.get_logger(__name__)


class HeistTask(BaseNTETask, TriggerTask):
    CONF_PICK_KEY = "触发按键"
    CONF_USE_SCROLL = "使用滚轮加速拾取"
    CONF_QUICK_RUN = "切换角色快速奔跑"
    CONF_QUICK_RUN_CHAR_COUNT = "快速奔跑角色数量"
    SEND_KEY_INTERVAL = 0.2
    CHECK_INTERVAL = 0.01
    PICK_KEY_HOLD_INTERVAL = 0.35
    QUICK_RUN_HOLD_INTERVAL = 0.5
    QUICK_RUN_KEY_AFTER_SLEEP = 0.25
    QUICK_RUN_SHIFT_INTERVAL = 0.2
    LISTENER_KEY_LOG_INTERVAL = 2
    PICK_STATE_LOG_INTERVAL = 2
    QUICK_RUN_STATE_LOG_INTERVAL = 2
    VK_CODE_SLOT_COUNT = 3
    KEY_MAP = {
        "shift": (win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT),
        "lshift": (win32con.VK_LSHIFT,),
        "rshift": (win32con.VK_RSHIFT,),
    }
    KEY_DOWN_MESSAGES = (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN)
    KEY_UP_MESSAGES = (win32con.WM_KEYUP, win32con.WM_SYSKEYUP)
    SHIFT_KEYS = (win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT)
    PYNPUT_KEY_MAP = {
        "shift": Key.shift_l,
        "lshift": Key.shift_l,
        "rshift": Key.shift_r,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self._submitted = False
        self._scroll_time = self.clock.UNSET
        self._scroll_switch = False
        self._scroll_count = 0
        self._pick_key_pressed = False
        self._pick_key_down_time = self.clock.UNSET
        self._next_pick_time = self.clock.UNSET
        self._shift_pressed = False
        self._shift_down_time = self.clock.UNSET
        self._quick_running = False
        self._quick_run_index = 0
        self._quick_run_time = self.clock.UNSET
        self._quick_run_step = 0
        self.name = "粉爪大劫案"
        self.description = "粉爪大劫案便利性功能"
        self.default_config.update(
            {
                self.CONF_PICK_KEY: "f",
                self.CONF_USE_SCROLL: True,
                self.CONF_QUICK_RUN: True,
                self.CONF_QUICK_RUN_CHAR_COUNT: 4,
            }
        )
        self.config_description.update(
            {
                self.CONF_PICK_KEY: "触发连点的按键 (按住生效)",
                self.CONF_USE_SCROLL: "触发连点将同步生效",
                self.CONF_QUICK_RUN: "按住Shift生效",
                self.CONF_QUICK_RUN_CHAR_COUNT: "切换角色数量",
            }
        )
        self._loop = True
        self.pynput = Controller()
        self.listener = None
        self.physical_keys_pressed = set()
        self.suppressed_keys = set()

    def run(self):
        self.start_listener()
        if not self.scene.is_in_team(self.is_in_team):
            self._loop = False
            return
        self._loop = True
        if self._submitted:
            return
        self._submitted = True
        self.submit_periodic_task(self.CHECK_INTERVAL, self._spam_key_loop)

    def alternate_scroll(self, interval=0):
        if not self.config.get(self.CONF_USE_SCROLL):
            return
        if self.clock.now() - self._scroll_time >= interval:
            time.sleep(0.01)
            if self._scroll_switch:
                self.scroll(0, 0, 1)
            else:
                self.scroll(0, 0, -1)
            self._scroll_time = self.clock.now()
            self._scroll_count += 1
            if self._scroll_count >= 3:
                self._scroll_count = 0
                self._scroll_switch = not self._scroll_switch

    def _is_onetime_task_running(self):
        if self.executor.current_task in self.executor.onetime_tasks:
            return self.executor.current_task.running

    def _spam_key_loop(self):
        if not self.enabled or self._is_onetime_task_running():
            self._submitted = False
            self._reset_state()
            return False

        if not self._is_active():
            self._reset_state()
            return True

        self._handle_quick_run()
        self._handle_pick_key()
        return True

    def _is_active(self):
        return self._loop and self.is_foreground()

    def _handle_pick_key(self):
        key = self._get_pick_key()
        key_pressed = self._is_key_pressed(key)
        now = self.clock.now()
        if not key_pressed:
            if self._is_key_down_by_async_state(key):
                self._log_diagnostic(
                    "pick_listener_missed",
                    (
                        f"heist pick key is down by async state but missing from listener state: "
                        f"key={key}, vk_codes={self._get_valid_vk_codes(key)}, "
                        f"physical_keys={sorted(self.physical_keys_pressed)}, "
                        f"listener_running={self._is_listener_running()}"
                    ),
                    self.PICK_STATE_LOG_INTERVAL,
                    level="warning",
                )
            self._reset_pick_key()
            return

        if not self._pick_key_down_time:
            self._pick_key_down_time = now
            self._log_diagnostic(
                "pick_key_hold_started",
                f"heist pick key hold detected: key={key}",
                self.PICK_STATE_LOG_INTERVAL,
            )
            return
        if now - self._pick_key_down_time < self.PICK_KEY_HOLD_INTERVAL:
            return
        if not self._pick_key_pressed:
            self._scroll_switch = False
            self._scroll_count = 0
            self._pick_key_pressed = True
            self._release_key(key)
            self._log_diagnostic(
                "quick_pick_started",
                f"heist quick pick started: key={key}",
                self.PICK_STATE_LOG_INTERVAL,
            )

        if now < self._next_pick_time:
            return
        if self._tap_key(key):
            self._next_pick_time = now + self.SEND_KEY_INTERVAL
        self.alternate_scroll(interval=self.SEND_KEY_INTERVAL)

    def _reset_pick_key(self):
        self._pick_key_pressed = False
        self._pick_key_down_time = self.clock.UNSET
        self._next_pick_time = self.clock.UNSET

    def _get_pick_key(self):
        return self.config.get(self.CONF_PICK_KEY)

    def _handle_quick_run(self):
        if not self.config.get(self.CONF_QUICK_RUN):
            self._reset_quick_run()
            return

        shift_pressed = self._is_key_pressed("shift")
        now = self.clock.now()
        if not shift_pressed:
            if self._is_key_down_by_async_state("shift"):
                self._log_diagnostic(
                    "quick_run_listener_missed",
                    (
                        f"heist quick run key is down by async state "
                        f"but missing from listener state: "
                        f"key=shift, vk_codes={self._get_valid_vk_codes('shift')}, "
                        f"physical_keys={sorted(self.physical_keys_pressed)}, "
                        f"listener_running={self._is_listener_running()}"
                    ),
                    self.QUICK_RUN_STATE_LOG_INTERVAL,
                    level="warning",
                )
            self._reset_quick_run()
            return
        if not self._shift_pressed:
            self._shift_down_time = now
            self._log_diagnostic(
                "quick_run_key_hold_started",
                "heist quick run key hold detected: key=shift",
                self.QUICK_RUN_STATE_LOG_INTERVAL,
            )

        self._shift_pressed = shift_pressed

        if not self._quick_running:
            if now - self._shift_down_time >= self.QUICK_RUN_HOLD_INTERVAL:
                self._quick_running = True
                self._quick_run_index = 0
                self._quick_run_time = self.clock.UNSET
                self._quick_run_step = 0
                self._release_shift_keys()
                self._log_diagnostic(
                    "quick_run_started",
                    "heist quick run started: key=shift",
                    self.QUICK_RUN_STATE_LOG_INTERVAL,
                )
            else:
                return

        try:
            char_count = int(self.config.get(self.CONF_QUICK_RUN_CHAR_COUNT))
        except (TypeError, ValueError):
            char_count = 4
        char_count = max(1, min(4, char_count))
        if now < self._quick_run_time:
            return

        if not self._is_key_pressed("shift"):
            self._reset_quick_run()
            return
        if self._quick_run_step == 0:
            key = str(self._quick_run_index % char_count + 1)
            self._quick_run_index += 1
            max_time = self.clock.timer(timeout=3)
            timer = self.clock.timer(timeout=self.QUICK_RUN_KEY_AFTER_SLEEP)
            next_send = self.clock.UNSET
            self.scene.clear_health_snapshot()
            while (
                self._is_active()
                and self._is_key_pressed("shift")
                and not max_time.expired()
            ):
                frame = self.next_frame()
                if frame is not None:
                    timer.extend(0.1)
                    if self.is_health_changed(frame):
                        break
                    if self.is_char_at_index(index=int(key) - 1, frame=frame):
                        break

                if self.clock.now() >= next_send:
                    self._tap_key(key)
                    next_send = self.clock.now() + 0.5

                if timer.expired():
                    break
                time.sleep(0.05)
            self._quick_run_step = 1
            self._quick_run_time = self.clock.now()
        else:
            elapsed = self.clock.now() - self._quick_run_time
            if elapsed >= 0.7:
                self._quick_run_step = 0
                self._quick_run_time = self.clock.now()
                return
            if elapsed >= (self._quick_run_step - 1) * self.QUICK_RUN_SHIFT_INTERVAL:
                self._tap_key("lshift")
                self._quick_run_step += 1

    def _reset_quick_run(self):
        self._shift_pressed = False
        self._shift_down_time = self.clock.UNSET
        self._quick_running = False
        self._quick_run_index = 0
        self._quick_run_time = self.clock.UNSET
        self._quick_run_step = 0

    def _reset_state(self, clear_physical_keys=False):
        self._reset_pick_key()
        self._reset_quick_run()
        self.suppressed_keys.clear()
        if clear_physical_keys:
            self.physical_keys_pressed.clear()

    def _get_vk_codes(self, key):
        vk_codes = ()
        if key is None:
            return (None,) * self.VK_CODE_SLOT_COUNT

        key = str(key).strip().lower()
        if key in self.KEY_MAP:
            vk_codes = self.KEY_MAP[key]
        elif key.startswith("f") and key[1:].isdigit():
            index = int(key[1:])
            if 1 <= index <= 12:
                vk_codes = (win32con.VK_F1 + index - 1,)
        elif len(key) == 1:
            vk_code = win32api.VkKeyScan(key)
            if vk_code != -1:
                vk_codes = (vk_code & 0xFF,)

        return vk_codes + (None,) * (self.VK_CODE_SLOT_COUNT - len(vk_codes))

    def _get_valid_vk_codes(self, key):
        return tuple(vk_code for vk_code in self._get_vk_codes(key) if vk_code is not None)

    def _get_pynput_key(self, key):
        key = str(key).strip().lower()
        if key in self.PYNPUT_KEY_MAP:
            return self.PYNPUT_KEY_MAP[key]
        if key.startswith("f") and key[1:].isdigit():
            index = int(key[1:])
            if 1 <= index <= 12:
                return getattr(Key, key)
        if len(key) == 1:
            return key
        return None

    def _tap_key(self, key):
        key = self._get_pynput_key(key)
        if key is None:
            return False
        self.pynput.press(key)
        try:
            time.sleep(0.02)
        finally:
            self.pynput.release(key)
        return True

    def _release_key(self, key):
        key = self._get_pynput_key(key)
        if key is None:
            return False
        self.pynput.release(key)
        return True

    def _release_shift_keys(self):
        self._release_key("lshift")
        self._release_key("rshift")

    def disable(self):
        self.stop_listener()
        super().disable()

    def start_listener(self):
        if self._check_listener_error():
            self.stop_listener()
        if self.listener is None or not self.listener.is_alive() or not self.listener.running:
            self.listener = keyboard.Listener(
                win32_event_filter=self._win32_filter,
            )
            self.physical_keys_pressed = set()
            self.suppressed_keys = set()
            self._log_diagnostic(
                "listener_starting",
                "heist keyboard listener starting",
                interval=10,
            )
            self.listener.start()
            self.listener.wait()
            if self._check_listener_error():
                self.stop_listener()
                return
            self._log_diagnostic(
                "listener_started",
                (
                    f"heist keyboard listener started: "
                    f"running={self.listener.running}, alive={self.listener.is_alive()}"
                ),
                interval=10,
            )

    def stop_listener(self):
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self._submitted = False
        self._reset_state(clear_physical_keys=True)

    def _win32_filter(self, msg, data):
        if data.flags & 0x10:
            return True

        self._log_target_key_event(msg, data)

        if msg in self.KEY_DOWN_MESSAGES:
            self.physical_keys_pressed.add(data.vkCode)
        elif msg in self.KEY_UP_MESSAGES:
            self.physical_keys_pressed.discard(data.vkCode)
            if data.vkCode in self.SHIFT_KEYS and not self._is_key_pressed("shift"):
                self._reset_quick_run()

        listener = self.listener
        should_suppress = listener is not None and self._should_suppress(msg, data.vkCode)
        if should_suppress:
            listener.suppress_event()
        return not should_suppress

    def _is_key_pressed(self, key):
        return any(
            vk_code in self.physical_keys_pressed for vk_code in self._get_valid_vk_codes(key)
        )

    def _is_key_down_by_async_state(self, key):
        return any(
            bool(win32api.GetAsyncKeyState(vk_code) & 0x8000)
            for vk_code in self._get_valid_vk_codes(key)
        )

    def _should_suppress(self, msg, vk_code):
        if msg in self.KEY_UP_MESSAGES:
            should_suppress = vk_code in self.suppressed_keys
            self.suppressed_keys.discard(vk_code)
            return should_suppress
        if msg not in self.KEY_DOWN_MESSAGES or not self._is_active():
            return False
        should_suppress = vk_code in self._suppressed_trigger_keys()
        if should_suppress:
            self.suppressed_keys.add(vk_code)
        return should_suppress

    def _suppressed_trigger_keys(self):
        keys = set()
        if self._pick_key_pressed:
            keys.update(self._get_valid_vk_codes(self._get_pick_key()))
        if self._quick_running:
            keys.update(self.SHIFT_KEYS)
        return keys

    def _is_listener_running(self):
        return self.listener is not None and self.listener.running and self.listener.is_alive()

    def _check_listener_error(self):
        if self.listener is None:
            return False
        try:
            self.listener.join(0)
            return False
        except Exception as e:
            logger.error("heist keyboard listener stopped with exception", e)
            return True

    def _log_target_key_event(self, msg, data):
        if msg not in self.KEY_DOWN_MESSAGES + self.KEY_UP_MESSAGES:
            return
        target_name = None
        if data.vkCode in self._get_valid_vk_codes(self._get_pick_key()):
            target_name = "pick"
        elif data.vkCode in self.SHIFT_KEYS:
            target_name = "quick run"
        if target_name is None:
            return
        action = "down" if msg in self.KEY_DOWN_MESSAGES else "up"
        self._log_diagnostic(
            f"listener_{target_name.replace(' ', '_')}_key_{action}",
            (
                f"heist listener received {target_name} key {action}: "
                f"vk={data.vkCode}, flags={data.flags}, active={self._is_active()}"
            ),
            self.LISTENER_KEY_LOG_INTERVAL,
        )

    def _log_diagnostic(self, key, message, interval, level="debug"):
        self.log_gated(
            level,
            message,
            interval=interval,
            key=("heist_diagnostic", key),
        )

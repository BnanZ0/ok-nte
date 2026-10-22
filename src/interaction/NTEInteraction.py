import ctypes
import threading
import time
from typing import Any

import win32api
import win32con
import win32gui
from ok import og
from ok.device.intercation import (
    INPUT,
    MOUSEINPUT,
    PostMessageInteraction,
    SendInput,
)
from ok.util.logger import Logger
from win32api import GetCursorPos

from src.interaction.cursor_sync import CursorSync
from src.interaction.keyboard_layout import QwertyPhysicalKeyMapper
from src.interaction.mouse_blocker import MouseInputBlocker
from src.utils import clock

logger = Logger.get_logger(__name__)


class NTEInteraction(PostMessageInteraction):
    _ACTIVATE_REFRESH_INTERVAL = 60 * 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None
        self._operating = False
        self._input_lock = threading.RLock()
        self._mouse_blocker = MouseInputBlocker()
        self._mouse_block_depth = 0
        self.user32 = ctypes.windll.user32
        self.qwerty_physical_key_mapper = QwertyPhysicalKeyMapper()
        self._disable_key_mapping = 0
        self._activate_require = True
        self._next_try_activate_at = clock.UNSET
        self._cursor_sync = CursorSync()
        self._cursor_sync.start()
        self.hwnd_window.visible_monitors.append(self)

    def on_visible(self, visible):
        self._activate_require = not visible

    def on_destroy(self):
        try:
            self._mouse_blocker.close()
        finally:
            self._cursor_sync.stop()
            super().on_destroy()

    def send_key(self, key, down_time=0.01):
        with self._input_lock:
            key = self._map_key(key)
            self._disable_key_mapping += 1
            try:
                return super().send_key(key, down_time=down_time)
            finally:
                self._disable_key_mapping -= 1
    
    def send_key_down(self, key, activate=True) -> Any:
        with self._input_lock:
            key = self._map_key(key)
            if activate:
                self.try_activate()
            vk_code = self.get_key_by_str(key)
            lparam = self.make_lparam(vk_code, is_up=False)
            return self.post(win32con.WM_KEYDOWN, vk_code, lparam)

    def send_key_up(self, key) -> Any:
        with self._input_lock:
            key = self._map_key(key)
            vk_code = self.get_key_by_str(key)
            lparam = self.make_lparam(vk_code, is_up=True)
            return self.post(win32con.WM_KEYUP, vk_code, lparam)

    def post(self, message, wParam=0, lParam=0, hwnd=None) -> Any:
        try:
            if hwnd is None:
                hwnd = self.hwnd
            win32gui.PostMessage(hwnd, message, wParam, lParam)
        except Exception as exc:
            logger.error(f"PostMessage failed for message {message:#x}: {exc}")
            return False
        return True

    def scroll(self, x, y, scroll_amount):
        with self._input_lock:
            self.try_activate()
            # logger.debug(f"scroll {x}, {y}, {scroll_amount}")

            base_hwnd = (
                self.hwnd_window.top_hwnd if self.hwnd_window.top_hwnd else self.hwnd_window.hwnd
            )
            if x > 0 and y > 0:
                top_x, top_y = self.hwnd_window.get_top_window_cords(x, y)
                abs_x, abs_y = win32gui.ClientToScreen(base_hwnd, (int(top_x), int(top_y)))
                self.bg_mouse_pos = (top_x, top_y)
                self._dynamic_target_hwnd = self._target_hwnd_at(abs_x, abs_y, base_hwnd)
                long_position = win32api.MAKELONG(abs_x, abs_y)
            else:
                self._dynamic_target_hwnd = base_hwnd
                long_position = 0

            wparam = win32api.MAKELONG(0, win32con.WHEEL_DELTA * scroll_amount)
            self.post(win32con.WM_MOUSEWHEEL, wparam, long_position)

    def _target_hwnd_at(self, abs_x, abs_y, fallback_hwnd):
        for hwnd_info in getattr(self.hwnd_window, "hwnds", []):
            candidate = hwnd_info[0]
            if not win32gui.IsWindow(candidate):
                continue
            try:
                left = hwnd_info[4]
                top = hwnd_info[5]
                right = left + hwnd_info[2]
                bottom = top + hwnd_info[3]
                if left <= abs_x < right and top <= abs_y < bottom:
                    return candidate
            except Exception:
                continue
        return fallback_hwnd

    def _map_key(self, key):
        if self._disable_key_mapping or not og.global_config.get_config("Game Hotkey Config").get(
            "Use QWERTY Physical Keys", False
        ):
            return key

        return self.qwerty_physical_key_mapper.map_key(key) or key

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.01, move=True, key="left"):
        with self._input_lock:
            self.try_activate()
            if x < 0:
                x, y = round(self.capture.width * 0.5), round(self.capture.height * 0.5)

            should_restore = move and move_back and not self._operating
            if move:
                if should_restore:
                    self.cursor_position = GetCursorPos()
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                self._cursor_sync.set_cursor_pos((abs_x, abs_y))
                time.sleep(0.035)
            click_pos = win32api.MAKELONG(x, y)
            if key == "left":
                btn_down = win32con.WM_LBUTTONDOWN
                btn_mk = win32con.MK_LBUTTON
                btn_up = win32con.WM_LBUTTONUP
            elif key == "middle":
                btn_down = win32con.WM_MBUTTONDOWN
                btn_mk = win32con.MK_MBUTTON
                btn_up = win32con.WM_MBUTTONUP
            else:
                btn_down = win32con.WM_RBUTTONDOWN
                btn_mk = win32con.MK_RBUTTON
                btn_up = win32con.WM_RBUTTONUP
            self.post(btn_down, btn_mk, click_pos)
            time.sleep(down_time)
            self.post(btn_up, 0, click_pos)
            if should_restore:
                self._restore_cursor()

    def operate(self, fun, block=False, restore_cursor=True):
        with self._input_lock:
            result = None
            reset_scene_executor = None
            is_outer_operate = not self._operating
            cursor_saved = False
            input_blocked = False
            try:
                if block:
                    self.block_input()
                    input_blocked = True
                if is_outer_operate:
                    self.cursor_position = GetCursorPos()
                    self._operating = True
                    cursor_saved = True
                reset_scene_executor = self._enter_reset_scene_without_check()
                result = fun()
            except Exception as e:
                logger.error("operate exception", e)
                raise
            finally:
                try:
                    try:
                        self._exit_reset_scene_without_check(reset_scene_executor)
                    finally:
                        if cursor_saved:
                            self._operating = False
                            if restore_cursor:
                                self._restore_cursor()
                finally:
                    if input_blocked:
                        self.unblock_input()
            return result

    def _enter_reset_scene_without_check(self):
        device_manager = getattr(self.hwnd_window, "device_manager", None)
        executor = getattr(device_manager, "executor", None)
        if executor is None:
            return None

        if not hasattr(executor, "_nte_operate_reset_scene_depth"):
            executor._nte_operate_reset_scene_depth = 0

        if not hasattr(executor, "_nte_original_reset_scene"):
            executor._nte_original_reset_scene = executor.reset_scene

            def reset_scene(check_enabled=True):
                if executor._nte_operate_reset_scene_depth > 0:
                    check_enabled = False
                return executor._nte_original_reset_scene(check_enabled=check_enabled)

            executor.reset_scene = reset_scene

        executor._nte_operate_reset_scene_depth += 1
        return executor

    def _exit_reset_scene_without_check(self, executor):
        if executor is None:
            return
        executor._nte_operate_reset_scene_depth = max(
            0, executor._nte_operate_reset_scene_depth - 1
        )

    def _restore_cursor(self):
        time.sleep(0.035)
        try:
            self._cursor_sync.set_cursor_pos(self.cursor_position)
        except Exception as e:
            logger.error("restore cursor exception", e)

    def block_input(self):
        """Block physical mouse input only; keyboard input remains available."""
        with self._input_lock:
            if self._mouse_block_depth == 0:
                self._mouse_blocker.start()
            self._mouse_block_depth += 1

    def unblock_input(self):
        with self._input_lock:
            if self._mouse_block_depth == 0:
                return
            self._mouse_block_depth -= 1
            if self._mouse_block_depth == 0:
                self._mouse_blocker.stop()

    def move_mouse_relative(self, dx, dy):
        """
        Moves the mouse cursor relative to its current position using user32.SendInput.

        Args:
            dx: The number of pixels to move the mouse horizontally.
                (positive for right, negative for left).
            dy: The number of pixels to move the mouse vertically.
                (positive for down, negative for up).
        """

        self._cursor_sync.mark_internal_move()
        mi = MOUSEINPUT(dx, dy, 0, 1, 0, None)
        i = INPUT(0, mi)  # type=0 indicates a mouse event
        return SendInput(1, ctypes.pointer(i), ctypes.sizeof(INPUT))

    def try_activate(self):
        now = clock.now()
        if self._activate_require:
            if not self.hwnd_window.is_foreground():
                super().try_activate()
                self._next_try_activate_at = now + self._ACTIVATE_REFRESH_INTERVAL
            self._activate_require = False
        elif now >= self._next_try_activate_at:
            super().try_activate()
            self._next_try_activate_at = now + self._ACTIVATE_REFRESH_INTERVAL

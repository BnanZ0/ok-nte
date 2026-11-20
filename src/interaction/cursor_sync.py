import ctypes
import threading
import time
from contextlib import suppress

import win32gui
from ok import og
from ok.util.logger import Logger
from win32api import GetCursorPos, SetCursorPos

from src.utils import clock

logger = Logger.get_logger(__name__)

_user32 = ctypes.windll.user32
_user32.SetCursorPos.restype = ctypes.c_int
_user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
_user32.GetCursorPos.restype = ctypes.c_int


def _win32_set_cursor_pos(position):
    """Set the cursor through user32 directly.

    pywin32's ``SetCursorPos`` wrapper can raise even when the underlying call
    succeeds, so call the Win32 API directly and report the real result.
    """
    return bool(_user32.SetCursorPos(int(position[0]), int(position[1])))


def _current_cursor_pos():
    point = ctypes.c_int * 2
    value = point()
    if _user32.GetCursorPos(ctypes.byref(value)):
        return (value[0], value[1])
    return None


_CURSOR_SYNC_INTERVAL = 0.02
_CURSOR_SYNC_CENTER_RATIO = 0.015
_CURSOR_SYNC_GEOMETRY_REFRESH_INTERVAL = 1.0
_CURSOR_SYNC_INTERNAL_MOVE_GRACE_PERIOD = 0.1
_CURSOR_SYNC_RESET_COOLDOWN = 0.15
_CURSOR_SYNC_CONFIG_NAME = "防止 NTE 移动鼠标"
_ENABLE_CURSOR_SYNC_KEY = "启用"
_CURSOR_SET_RETRIES = 3
_CURSOR_SET_TOLERANCE = 2


def _set_cursor_pos(position):
    """Move the cursor, retrying and verifying that it actually landed.

    A click posted to the game only registers when the real cursor is over the
    target; a rejected move silently downgrades it to a hover, so the move must
    be verified rather than assumed.
    """
    target = (int(position[0]), int(position[1]))
    for attempt in range(_CURSOR_SET_RETRIES):
        with suppress(Exception):
            # pywin32 wrapper may raise on success; the direct call is authoritative.
            SetCursorPos(target)
        if _win32_set_cursor_pos(target):
            current = _current_cursor_pos()
            if current is not None and (
                abs(current[0] - target[0]) <= _CURSOR_SET_TOLERANCE
                and abs(current[1] - target[1]) <= _CURSOR_SET_TOLERANCE
            ):
                return True
        if attempt + 1 < _CURSOR_SET_RETRIES:
            time.sleep(0.02)
    return False


class CursorSync:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._center = None
        self._limit = None
        self._ignore_until = clock.UNSET
        self._last_cursor_position = None
        self._last_sample_was_outside = False
        self._next_geometry_refresh_at = clock.UNSET
        self._next_reset_at = clock.UNSET
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="NTECursorSync",
            )
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._wake_event.set()

    def set_cursor_pos(self, position):
        """Move the cursor and report whether a click can land on the target.

        Returns True when the cursor is verified at ``position``. Moves used for
        click targeting must succeed for the click to register, so a failure is
        logged instead of being swallowed.
        """
        with self._lock:
            is_in_center_zone = self._is_in_center_zone(position)
            if self._center is not None and not is_in_center_zone:
                self._last_cursor_position = tuple(position)
            self._last_sample_was_outside = self._center is not None and not is_in_center_zone
            self._ignore_until = clock.now() + _CURSOR_SYNC_INTERNAL_MOVE_GRACE_PERIOD
        moved = _set_cursor_pos(position)
        if not moved:
            logger.warning(f"set_cursor_pos could not reach {position}")
        with self._lock:
            self._ignore_until = clock.now()
        self._wake_event.set()
        return moved

    def mark_internal_move(self):
        with self._lock:
            self._ignore_until = clock.now() + _CURSOR_SYNC_INTERNAL_MOVE_GRACE_PERIOD
        self._wake_event.set()

    def _worker(self):
        while not self._stop_event.is_set():
            is_game_foreground = True
            with suppress(Exception):
                is_game_foreground = self._is_game_foreground()
            if not self._has_game_window() or is_game_foreground or not self._is_enabled():
                self._wait()
                continue

            self._refresh_geometry_if_needed()
            cursor_position = None
            with suppress(Exception):
                cursor_position = GetCursorPos()
            if cursor_position is not None:
                self._sync_cursor_position(cursor_position)
            self._wait()

    def _refresh_geometry_if_needed(self):
        now = clock.now()
        with self._lock:
            if now < self._next_geometry_refresh_at:
                return
            self._next_geometry_refresh_at = now + _CURSOR_SYNC_GEOMETRY_REFRESH_INTERVAL

        geometry = None
        with suppress(Exception):
            geometry = self._get_geometry()
        if geometry is not None:
            with self._lock:
                self._center, self._limit = geometry

    def _sync_cursor_position(self, cursor_position):
        reset_position = None
        with self._lock:
            is_in_center_zone = self._is_in_center_zone(cursor_position)
            now = clock.now()
            should_reset = all(
                (
                    now >= self._ignore_until,
                    is_in_center_zone,
                    self._last_sample_was_outside,
                    self._last_cursor_position is not None,
                    now >= self._next_reset_at,
                )
            )
            if should_reset:
                reset_position = self._last_cursor_position
                self._next_reset_at = now + _CURSOR_SYNC_RESET_COOLDOWN
            elif not is_in_center_zone:
                self._last_cursor_position = cursor_position
            self._last_sample_was_outside = not is_in_center_zone

        if reset_position is not None:
            self.set_cursor_pos(reset_position)

    def _is_in_center_zone(self, position):
        if self._center is None or self._limit is None:
            return False
        x, y = position
        center_x, center_y = self._center
        limit_x, limit_y = self._limit
        return abs(x - center_x) <= limit_x and abs(y - center_y) <= limit_y

    def _is_game_foreground(self):
        return og.device_manager.hwnd_window.is_foreground()

    def _has_game_window(self):
        hwnd_window = getattr(getattr(og, "device_manager", None), "hwnd_window", None)
        return bool(getattr(hwnd_window, "hwnd", 0))

    def _is_enabled(self):
        global_config = getattr(og, "global_config", None)
        if global_config is None:
            return True
        try:
            return global_config.get_config(_CURSOR_SYNC_CONFIG_NAME).get(
                _ENABLE_CURSOR_SYNC_KEY, True
            )
        except Exception:
            return True

    def _get_geometry(self):
        hwnd_window = og.device_manager.hwnd_window
        if not hwnd_window.hwnd:
            return None
        width = og.executor.method.width
        height = og.executor.method.height
        center_x, center_y = hwnd_window.get_top_window_cords(width * 0.5, height * 0.5)
        base_hwnd = hwnd_window.top_hwnd or hwnd_window.hwnd
        abs_center = win32gui.ClientToScreen(base_hwnd, (round(center_x), round(center_y)))
        return abs_center, (width * _CURSOR_SYNC_CENTER_RATIO, height * _CURSOR_SYNC_CENTER_RATIO)

    def _wait(self):
        self._wake_event.wait(_CURSOR_SYNC_INTERVAL)
        self._wake_event.clear()

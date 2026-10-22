import threading
from contextlib import suppress

import win32gui
from ok import og
from win32api import GetCursorPos, SetCursorPos

from src.utils import clock

_CURSOR_SYNC_INTERVAL = 0.02
_CURSOR_SYNC_CENTER_RATIO = 0.015
_CURSOR_SYNC_GEOMETRY_REFRESH_INTERVAL = 1.0
_CURSOR_SYNC_INTERNAL_MOVE_GRACE_PERIOD = 0.1
_CURSOR_SYNC_RESET_COOLDOWN = 0.15
_CURSOR_SYNC_CONFIG_NAME = "防止 NTE 移动鼠标"
_ENABLE_CURSOR_SYNC_KEY = "启用"


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
        with self._lock:
            is_in_center_zone = self._is_in_center_zone(position)
            if self._center is not None and not is_in_center_zone:
                self._last_cursor_position = tuple(position)
            self._last_sample_was_outside = self._center is not None and not is_in_center_zone
            self._ignore_until = clock.now() + _CURSOR_SYNC_INTERNAL_MOVE_GRACE_PERIOD
        SetCursorPos(position)
        with self._lock:
            self._ignore_until = clock.now()
        self._wake_event.set()

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

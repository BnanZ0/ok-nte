import os
from dataclasses import dataclass
from threading import Thread

import psutil
import win32process
from ok.core.events import EventSignal
from ok.util.handler import ExitEvent
from ok.util.window import is_foreground_window

from src import GAME_EXE, get_game_target_path
from src.utils import clock

_POLL_INTERVAL_SECONDS = 0.25
_RESTORE_RETRY_SECONDS = 2


@dataclass(frozen=True)
class BrowserWindow:
    exe_path: str
    hwnd: int
    pid: int

    def is_foreground(self) -> bool:
        return is_foreground_window(self.hwnd)

    def process_ids(self) -> set[int]:
        try:
            browser = psutil.Process(self.pid)
            if os.path.normcase(browser.exe()) == self.exe_path:
                return {self.pid, *(child.pid for child in browser.children(recursive=True))}
        except psutil.Error:
            pass
        return set()


def browser_window_from_target(exe_path: str | None, hwnd: int | None) -> BrowserWindow | None:
    if exe_path and hwnd:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid:
                return BrowserWindow(os.path.normcase(exe_path), hwnd, pid)
        except win32process.error:
            pass
    return None


class BrowserWindowMonitor:
    def __init__(self, create_listener, restore_native, get_exit_event):
        self._create_listener = create_listener
        self._restore_native = restore_native
        self._get_exit_event = get_exit_event
        self.focus_changed = EventSignal("browser_window_focus")
        self.listener = None
        self._window: BrowserWindow | None = None
        self._pending_restores = {}

    def start(self, *_args) -> None:
        exit_event = self._get_exit_event()
        if exit_event is not None and not exit_event.is_set():
            Thread(
                target=self._watch,
                args=(exit_event,),
                name="browser_audio_route_monitor",
                daemon=True,
            ).start()

    def _watch(self, exit_event: ExitEvent) -> None:
        browser_selected = False
        previous_focus = None
        retry = clock.timer(timeout=_RESTORE_RETRY_SECONDS)
        while not exit_event.is_set():
            exe_path, hwnd = get_game_target_path()
            selected = exe_path != GAME_EXE
            retry_now = retry.expired()
            if selected and (not browser_selected or retry_now):
                self._restore_native()
            browser_selected = selected
            window = browser_window_from_target(exe_path, hwnd)
            window_changed = window != self._window
            if window_changed:
                self._change_window(window, exit_event)
            if window is not None:
                foreground = window.is_foreground()
                if window_changed or foreground != previous_focus or retry_now:
                    self.focus_changed.emit(foreground)
                previous_focus = foreground
            if retry_now:
                for path, listener in tuple(self._pending_restores.items()):
                    if listener.restore_pending:
                        listener.retry_restore()
                    else:
                        del self._pending_restores[path]
                retry = clock.timer(timeout=_RESTORE_RETRY_SECONDS)
            exit_event.wait(_POLL_INTERVAL_SECONDS)

    def _change_window(self, window: BrowserWindow | None, exit_event: ExitEvent) -> None:
        if self.listener is not None:
            self.focus_changed.disconnect(self.listener.on_window)
            self.listener.request_restore()
            exit_event.unbind_stop(self.listener)
            if self.listener.restore_pending:
                self._pending_restores[self._window.exe_path] = self.listener
            self.listener = None
        self._window = window
        if window is not None:
            self.listener = self._pending_restores.pop(window.exe_path, None)
            if self.listener is None:
                self.listener = self._create_listener(window)
            else:
                self.listener.resume_browser_window(window)
            self.focus_changed.connect(self.listener.on_window)

    def restore_outputs(self) -> None:
        listener = self.listener
        if listener is not None:
            listener.stop()
        for listener in tuple(self._pending_restores.values()):
            listener.stop()

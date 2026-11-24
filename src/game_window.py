import os
from dataclasses import dataclass, field
from threading import Lock, Thread, current_thread

import psutil
import win32process
from ok import og
from ok.core.events import EventSignal
from ok.util.handler import ExitEvent
from ok.util.window import is_foreground_window

from src import GAME_EXE
from src.events import communicate
from src.utils import clock

_BROWSER_POLL_SECONDS = 0.25
_REPORT_INTERVAL_SECONDS = 2


@dataclass(frozen=True)
class GameWindow:
    """The selected game executable and its current window, if available."""

    exe: str
    hwnd: int | None = None
    foreground: bool | None = None
    _pid: int | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.exe)

    @property
    def identity(self) -> tuple[str, int | None, int | None]:
        return self.exe, self.hwnd, self._pid

    def process_ids(self) -> set[int]:
        if self._pid is not None:
            try:
                process = psutil.Process(self._pid)
                exe = process.exe() if os.path.isabs(self.exe) else process.name()
                if os.path.normcase(exe) == os.path.normcase(self.exe):
                    return {self._pid, *(child.pid for child in process.children(recursive=True))}
            except psutil.Error:
                pass
            return set()
        if os.path.isabs(self.exe):
            return set()
        name = self.name.casefold()
        return {
            process.pid
            for process in psutil.process_iter(["name"])
            if (process.info.get("name") or "").casefold() == name
        }


def get_exit_event() -> ExitEvent | None:
    if (exit_event := getattr(og, "exit_event", None)) is not None:
        return exit_event
    return getattr(getattr(og, "ok", None), "exit_event", None)


def _window_pid(hwnd: int | None) -> int | None:
    if hwnd:
        try:
            return win32process.GetWindowThreadProcessId(hwnd)[1] or None
        except win32process.error:
            pass
    return None


class GameWindowMonitor:
    def __init__(self):
        self.updated = EventSignal("game_window_updated")
        self._lock = Lock()
        self._current: GameWindow | None = GameWindow(GAME_EXE)
        self._started = False
        self._browser_selected = False
        self._worker: Thread | None = None
        self._next_report: clock.Timer | None = None

    @property
    def current(self) -> GameWindow | None:
        with self._lock:
            return self._current

    def start(self) -> None:
        manager = og.device_manager
        if manager is None:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        communicate.window.connect(self._on_native_window)
        config = manager.config
        previous_validator = config.validator

        # DeviceManager validates the selection before starting the new capture.
        def validate(key, value):
            valid, message = previous_validator(key, value) if previous_validator else (True, None)
            if valid and key == "preferred":
                self._select_browser(value == "browser")
            return valid, message

        config.validator = validate
        self._select_browser(config.get("preferred") == "browser")

    def _select_browser(self, selected: bool) -> None:
        with self._lock:
            changed = selected != self._browser_selected
            self._browser_selected = selected
        if not selected:
            self._on_native_window()
            return
        if changed:
            self._publish(None, browser=True)
        with self._lock:
            exit_event = get_exit_event()
            if not self._browser_selected or self._worker is not None:
                return
            if exit_event is None or exit_event.is_set():
                return
            self._worker = Thread(
                target=self._watch_browser,
                args=(exit_event,),
                name="browser_window_monitor",
                daemon=True,
            )
            self._worker.start()

    def _on_native_window(self, *_args) -> None:
        with self._lock:
            if self._browser_selected:
                return
        window = getattr(og.device_manager, "hwnd_window", None)
        exe = getattr(window, "exe_full_path", None)
        hwnd = getattr(window, "hwnd", None) or None
        target = GameWindow(GAME_EXE)
        if exe and os.path.basename(exe).casefold() == GAME_EXE.casefold():
            if pid := _window_pid(hwnd):
                target = GameWindow(GAME_EXE, hwnd, window.visible, pid)
        self._publish(target, browser=False)

    def _watch_browser(self, exit_event: ExitEvent) -> None:
        from ok.device.capture_methods.browser import BrowserCaptureMethod

        try:
            while not exit_event.is_set():
                with self._lock:
                    if not self._browser_selected:
                        self._worker = None
                        return
                capture = getattr(og.device_manager, "capture_method", None)
                window = None
                if isinstance(capture, BrowserCaptureMethod):
                    exe, hwnd = capture.exe_full_path, capture.hwnd
                    if exe and (pid := _window_pid(hwnd)):
                        window = GameWindow(
                            os.path.normcase(exe), hwnd, is_foreground_window(hwnd), pid
                        )
                self._publish(window, browser=True)
                exit_event.wait(_BROWSER_POLL_SECONDS)
        finally:
            with self._lock:
                if self._worker is current_thread():
                    self._worker = None

    def _publish(self, window: GameWindow | None, *, browser: bool) -> None:
        with self._lock:
            if browser != self._browser_selected:
                return
            if (
                window == self._current
                and self._next_report is not None
                and not self._next_report.expired()
            ):
                return
            self._current = window
            # A periodic refresh lets consumers retry work while focus stays unchanged.
            self._next_report = clock.timer(timeout=_REPORT_INTERVAL_SECONDS)
        self.updated.emit(window)


game_window = GameWindowMonitor()

import atexit
import threading
from dataclasses import dataclass
from typing import Literal

import psutil
from ok import ConfigOption, og
from ok.util.logger import Logger

from src import GAME_EXE, get_game_target_path
from src.audio import routing_config, svcl
from src.audio.browser_window import BrowserWindow, BrowserWindowMonitor, browser_window_from_target
from src.audio.routing_config import CONF_BACKGROUND_DEVICE, CONF_ENABLE, CONF_SVCL_PATH
from src.audio.routing_config import discover_output_devices as discover_output_devices
from src.audio.svcl import DEFAULT_RENDER_DEVICE as DEFAULT_RENDER_DEVICE
from src.events import communicate
from src.utils import clock

logger = Logger.get_logger(__name__)

_WINDOW_ROUTE_CHECK_INTERVAL_SECONDS = 2


def audio_route_command(device: str, process_name: str | int = GAME_EXE) -> list[str]:
    return svcl.audio_route_command(device, process_name)


def create_background_audio_routing_config_option() -> ConfigOption:
    option = routing_config.create_config_option(
        route_background_audio_for_current_window, restore_background_audio_router
    )
    connect_background_audio_router()
    return option


def _routing_config():
    return routing_config.get_config(
        getattr(og, "global_config", None), restore_background_audio_router
    )


def _target_process_ids(process_name: str) -> set[int]:
    process_name = process_name.casefold()
    process_ids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info.get("name") or "").casefold() == process_name:
                process_ids.add(int(proc.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, TypeError, ValueError):
            continue
    return process_ids


def connect_background_audio_router() -> None:
    _router.connect_window_signal()
    communicate.start_success.connect(_browser_monitor.start)


def restore_background_audio_router() -> None:
    browser_router = _browser_monitor.listener
    if browser_router is not None:
        browser_router.request_restore()
    _router.request_restore()


def route_background_audio_for_current_window() -> None:
    (_browser_monitor.listener or _router).route_current_window_state()


def _restore_routes() -> None:
    _browser_monitor.restore_outputs()
    _router.restore_on_exit()


@dataclass(frozen=True)
class _BackgroundRequest:
    device: str
    exe_path: str


type _RouteRequest = _BackgroundRequest | Literal["restore"]


@dataclass(frozen=True)
class _SavedRoute:
    request: _BackgroundRequest
    window: BrowserWindow | None
    original_device: str
    background_device: svcl.RenderOutputDevice
    original_default_device: svcl.RenderOutputDevice | None

    @property
    def needs_restore(self) -> bool:
        return self.original_device != self.background_device.route_device


class _BackgroundAudioRouter:
    def __init__(self, browser_window: BrowserWindow | None = None):
        self._browser_window = browser_window
        self._lock = threading.Lock()
        self._pending_route: _RouteRequest | None = None
        self._saved_route: _SavedRoute | None = None
        self._worker: threading.Thread | None = None
        self._connected = False
        self._bound_exit_event = None
        self._last_visible: bool | None = None
        self._last_native_check = clock.UNSET

    def on_window(self, visible: bool, *_args) -> None:
        window = self._browser_window
        if not self._is_current_target(window):
            if visible:
                self.request_restore()
            return
        if window is None:
            now = clock.now()
            recently_checked = now - self._last_native_check <= _WINDOW_ROUTE_CHECK_INTERVAL_SECONDS
            if visible == self._last_visible and recently_checked:
                return
            self._last_native_check = now
        self._last_visible = visible
        self._request_route(visible)

    def connect_window_signal(self) -> None:
        self._bind_exit_event()
        with self._lock:
            if self._connected:
                return
            communicate.window.connect(self.on_window)
            self._connected = True

    def route_current_window_state(self) -> None:
        self._bind_exit_event()
        visible = self._current_window_visible()
        if visible is not None:
            self._request_route(visible, enabled=True)

    def _request_route(self, visible: bool, enabled: bool | None = None) -> None:
        self._bind_exit_event()
        exit_event = _ok_exit_event()
        if exit_event is not None and exit_event.is_set():
            return
        config = _routing_config()
        if config is None:
            return
        if visible or not (config.get(CONF_ENABLE, False) if enabled is None else enabled):
            self.request_restore()
            return
        if not self._is_current_target(self._browser_window):
            return
        exe_path = config.get(CONF_SVCL_PATH, "")
        if not exe_path:
            logger.warning("background audio routing skipped: svcl.exe is not configured")
            return

        device = config.get(CONF_BACKGROUND_DEVICE)
        if not device:
            logger.warning("background audio routing skipped: target output device is empty")
            return

        self._submit_route(_BackgroundRequest(device, exe_path))

    def request_restore(self) -> threading.Thread | None:
        return self._submit_route("restore")

    def _submit_route(self, route: _RouteRequest) -> threading.Thread | None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                self._pending_route = route
                return self._worker
            if not self._route_needed(route):
                return None
            self._pending_route = route
            self._worker = threading.Thread(
                target=self._run_pending_routes,
                name="background_audio_router",
                daemon=True,
            )
            self._worker.start()
            return self._worker

    def _route_needed(self, route: _RouteRequest) -> bool:
        saved = self._saved_route
        if isinstance(route, _BackgroundRequest):
            return (
                saved is None
                or saved.window != self._browser_window
                or saved.request.device != route.device
            )
        return saved is not None and saved.needs_restore

    @staticmethod
    def _is_current_target(window: BrowserWindow | None) -> bool:
        exe_path, hwnd = get_game_target_path()
        if window is None:
            return exe_path == GAME_EXE
        return window == browser_window_from_target(exe_path, hwnd)

    def _bind_exit_event(self) -> None:
        exit_event = _ok_exit_event()
        if exit_event is None:
            return
        with self._lock:
            if self._bound_exit_event is exit_event:
                return
            self._bound_exit_event = exit_event
        # bind_stop may call stop() synchronously when the event is already set.
        # Do not invoke it while holding _lock, because stop() needs the same lock.
        exit_event.bind_stop(self)

    def stop(self) -> None:
        self.restore_on_exit()

    @property
    def restore_pending(self) -> bool:
        with self._lock:
            return self._worker is not None or self._route_needed("restore")

    def retry_restore(self) -> None:
        window = self._browser_window
        if window is not None and window.process_ids():
            self.request_restore()

    def resume_browser_window(self, window: BrowserWindow) -> None:
        # Keep the saved device for the reopened executable and refresh its PID and focus.
        with self._lock:
            self._browser_window = window
            self._last_visible = None
            self._bound_exit_event = None

    def _current_window_visible(self) -> bool | None:
        if self._browser_window is not None:
            return self._browser_window.is_foreground()
        with self._lock:
            if self._last_visible is not None:
                return self._last_visible
        hwnd_window = getattr(getattr(og, "device_manager", None), "hwnd_window", None)
        visible = getattr(hwnd_window, "visible", None)
        return visible if isinstance(visible, bool) else None

    def _run_pending_routes(self) -> None:
        while True:
            with self._lock:
                route = self._pending_route
                self._pending_route = None
                if route is None:
                    self._worker = None
                    return
                if not self._route_needed(route):
                    continue
                window = self._browser_window
                saved = self._saved_route
            if isinstance(route, _BackgroundRequest):
                if self._is_current_target(window):
                    self._route_to_background(route, window, saved)
            elif saved is not None:
                self._restore_output(saved, window)

    @staticmethod
    def _current_render_target(
        data: list[dict[str, str]], window: BrowserWindow | None
    ) -> tuple[svcl.RenderOutputDevice, str | int] | None:
        if window is not None:
            return svcl.find_render_session(data, window.process_ids())
        session = svcl.find_render_session(data, _target_process_ids(GAME_EXE))
        return (session[0], GAME_EXE) if session is not None else None

    def _route_to_background(
        self, request: _BackgroundRequest, window: BrowserWindow | None, saved: _SavedRoute | None
    ) -> None:
        try:
            data = svcl.list_sound_items(request.exe_path)
        except Exception as exc:
            logger.warning(f"failed to list svcl sound items before background route: {exc}")
            return

        current = self._current_render_target(data, window)
        if current is None:
            logger.warning("background audio routing skipped: current output device is unknown")
            return
        current_render_device, process = current

        target_device = svcl.resolve_output_device(data, request.device)
        if target_device is None:
            return

        default_device = svcl.default_output_device(data)
        original_device = (
            DEFAULT_RENDER_DEVICE
            if svcl.same_output_device(current_render_device, target_device)
            else current_render_device.route_device
        )
        original_default = (
            default_device
            if default_device and svcl.same_output_device(current_render_device, default_device)
            else None
        )
        if saved is not None:
            previous = self._background_output(saved, default_device)
            if previous is not None and svcl.same_output_device(current_render_device, previous):
                original_device = saved.original_device
                original_default = saved.original_default_device

        if not svcl.set_app_output(request.exe_path, target_device.route_device, process):
            return
        with self._lock:
            self._saved_route = _SavedRoute(
                request, window, original_device, target_device, original_default
            )
        logger.info(
            "saved game audio output for restore: "
            f"current={current_render_device.route_device} target={target_device.route_device} "
            f"restore={original_device}"
        )

    @staticmethod
    def _background_output(
        saved: _SavedRoute, default_device: svcl.RenderOutputDevice | None
    ) -> svcl.RenderOutputDevice | None:
        if saved.background_device.route_device == DEFAULT_RENDER_DEVICE:
            return default_device
        return saved.background_device

    def _restore_output(self, saved: _SavedRoute, window: BrowserWindow | None) -> None:
        exe_path = saved.request.exe_path
        try:
            data = svcl.list_sound_items(exe_path)
            current = self._current_render_target(data, window)
            default_device = svcl.default_output_device(data)
        except Exception as exc:
            logger.warning(f"game audio restore skipped: cannot read current output device: {exc}")
            return
        expected = self._background_output(saved, default_device)
        if expected is None:
            return
        if current is None:
            logger.warning("game audio restore skipped: current output device is unknown")
            return
        current_device, process = current
        device = saved.original_device
        logger.info(
            "check game audio output before restore: "
            f"current={current_device.route_device} background={expected.route_device} "
            f"restore={device} "
            f"system_default={default_device.route_device if default_device else 'unknown'}"
        )
        if not svcl.same_output_device(current_device, expected):
            logger.info("game audio output device changed externally; keeping the new selection")
        else:
            original_default = saved.original_default_device
            if (
                original_default is not None
                and default_device is not None
                and not svcl.same_output_device(original_default, default_device)
            ):
                logger.info(
                    "system default output changed while backgrounded; restoring system default"
                )
                device = DEFAULT_RENDER_DEVICE
            if not svcl.set_app_output(exe_path, device, process):
                return
        with self._lock:
            self._saved_route = None

    def restore_on_exit(self) -> None:
        worker = self.request_restore()
        if worker is not None and worker is not threading.current_thread():
            # A background switch and the following restore can each call SVCL twice.
            worker.join(timeout=4 * svcl.COMMAND_TIMEOUT_SECONDS + 0.5)


def _ok_exit_event():
    exit_event = getattr(og, "exit_event", None)
    if exit_event is not None:
        return exit_event
    ok_instance = getattr(og, "ok", None)
    return getattr(ok_instance, "exit_event", None)


_router = _BackgroundAudioRouter()
_browser_monitor = BrowserWindowMonitor(
    _BackgroundAudioRouter, _router.request_restore, _ok_exit_event
)
atexit.register(_restore_routes)

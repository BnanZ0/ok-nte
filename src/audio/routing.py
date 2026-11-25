import atexit
import threading
from dataclasses import dataclass
from typing import Literal

from ok import ConfigOption, og
from ok.util.logger import Logger

from src import GAME_EXE
from src.audio import routing_config, svcl
from src.audio.routing_config import CONF_BACKGROUND_DEVICE, CONF_ENABLE, CONF_SVCL_PATH
from src.audio.routing_config import discover_output_devices as discover_output_devices
from src.audio.svcl import DEFAULT_RENDER_DEVICE as DEFAULT_RENDER_DEVICE
from src.events import communicate
from src.game_window import GameWindow, game_window, get_exit_event

logger = Logger.get_logger(__name__)


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


def connect_background_audio_router() -> None:
    game_window.updated.connect(_router.on_window)
    communicate.start_success.connect(_router.bind_exit)


def restore_background_audio_router() -> None:
    _router.request_restore()


def route_background_audio_for_current_window() -> None:
    _router.on_window(game_window.current, enabled=True)


@dataclass(frozen=True)
class _BackgroundRequest:
    device: str
    exe_path: str


type _RouteRequest = _BackgroundRequest | Literal["restore", "keep"]


@dataclass(frozen=True)
class _SavedRoute:
    request: _BackgroundRequest
    window: GameWindow
    original_device: str
    background_device: svcl.RenderOutputDevice
    original_default_device: svcl.RenderOutputDevice | None

    @property
    def needs_restore(self) -> bool:
        return self.original_device != self.background_device.route_device


class _BackgroundAudioRouter:
    def __init__(self):
        self._requested_window: GameWindow | None = None
        self._lock = threading.Lock()
        self._pending_route: _RouteRequest | None = None
        self._saved_route: _SavedRoute | None = None
        self._worker: threading.Thread | None = None
        self._warned_unknown_output_for: tuple[str, int | None, int | None] | None = None

    def bind_exit(self, *_args) -> None:
        if exit_event := get_exit_event():
            exit_event.bind_stop(self)

    def on_window(self, window: GameWindow | None, enabled: bool | None = None) -> None:
        with self._lock:
            exit_event = get_exit_event()
            if window != game_window.current or (exit_event is not None and exit_event.is_set()):
                return
            self._requested_window = window
            self._submit_route_locked(self._route_for_window(window, enabled))

    @staticmethod
    def _route_for_window(window: GameWindow | None, enabled: bool | None) -> _RouteRequest:
        if window is None or window.foreground is not False:
            return "restore"
        config = _routing_config()
        if config is None:
            return "keep"
        if not (config.get(CONF_ENABLE, False) if enabled is None else enabled):
            return "restore"
        exe_path = config.get(CONF_SVCL_PATH, "")
        if not exe_path:
            logger.warning("background audio routing skipped: svcl.exe is not configured")
            return "keep"

        device = config.get(CONF_BACKGROUND_DEVICE)
        if not device:
            logger.warning("background audio routing skipped: target output device is empty")
            return "keep"

        return _BackgroundRequest(device, exe_path)

    def request_restore(self) -> threading.Thread | None:
        with self._lock:
            return self._submit_route_locked("restore")

    def _submit_route_locked(self, route: _RouteRequest) -> threading.Thread | None:
        if self._worker is not None and self._worker.is_alive():
            self._pending_route = route
            return self._worker
        saved = self._saved_route
        if not self._target_changed(saved) and not self._route_needed(route, saved):
            return None
        self._pending_route = route
        self._worker = threading.Thread(
            target=self._run_pending_routes,
            name="background_audio_router",
            daemon=True,
        )
        self._worker.start()
        return self._worker

    def _target_changed(self, saved: _SavedRoute | None) -> bool:
        window = self._requested_window
        return saved is not None and (window is None or saved.window.identity != window.identity)

    @staticmethod
    def _route_needed(route: _RouteRequest, saved: _SavedRoute | None) -> bool:
        if isinstance(route, _BackgroundRequest):
            return saved is None or saved.request.device != route.device
        return route == "restore" and saved is not None and saved.needs_restore

    def _run_pending_routes(self) -> None:
        while True:
            with self._lock:
                route = self._pending_route
                if route is None:
                    self._worker = None
                    return
                window = self._requested_window
                saved = self._saved_route
                if saved is not None and self._target_changed(saved):
                    # Leave the latest request queued while restoring the previous target.
                    previous = saved
                else:
                    previous = None
                    self._pending_route = None
                    if not self._route_needed(route, saved):
                        continue
            if previous is not None:
                self._restore_previous_target(previous)
                continue
            if isinstance(route, _BackgroundRequest):
                if window is not None and window == game_window.current:
                    self._route_to_background(route, window, saved)
            elif saved is not None and self._restore_output(saved):
                with self._lock:
                    self._saved_route = None

    def _restore_previous_target(self, saved: _SavedRoute) -> None:
        restored = not saved.needs_restore or self._restore_output(saved)
        if not restored:
            exit_event = get_exit_event()
            if (exit_event is None or not exit_event.is_set()) and saved.window.process_ids():
                restored = self._restore_output(saved)
                if not restored:
                    logger.warning("game audio restore failed after retry")
        with self._lock:
            if restored or self._target_changed(saved):
                self._saved_route = None

    @staticmethod
    def _read_output(
        exe_path: str, window: GameWindow
    ) -> tuple[list[dict[str, str]], svcl.RenderOutputDevice, str | int] | None:
        pids = window.process_ids()
        if not pids:
            return None
        data = svcl.list_sound_items(exe_path)
        session = svcl.find_render_session(data, pids)
        if session is None:
            return None
        device, pid = session
        return data, device, pid if window.hwnd else window.exe

    def _route_to_background(
        self, request: _BackgroundRequest, window: GameWindow, saved: _SavedRoute | None
    ) -> None:
        try:
            current = self._read_output(request.exe_path, window)
        except Exception as exc:
            logger.warning(f"failed to list svcl sound items before background route: {exc}")
            return

        if current is None:
            if self._warned_unknown_output_for != window.identity:
                self._warned_unknown_output_for = window.identity
                logger.warning("background audio routing skipped: current output device is unknown")
            return
        self._warned_unknown_output_for = None
        data, current_render_device, process = current

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

    def _restore_output(self, saved: _SavedRoute) -> bool:
        exe_path = saved.request.exe_path
        try:
            current = self._read_output(exe_path, saved.window)
        except Exception as exc:
            logger.warning(f"game audio restore skipped: cannot read current output device: {exc}")
            return False
        if current is None:
            logger.warning("game audio restore skipped: current output device is unknown")
            return False
        data, current_device, process = current
        default_device = svcl.default_output_device(data)
        expected = self._background_output(saved, default_device)
        if expected is None:
            return False
        device = saved.original_device
        logger.info(
            "check game audio output before restore: "
            f"current={current_device.route_device} background={expected.route_device} "
            f"restore={device} "
            f"system_default={default_device.route_device if default_device else 'unknown'}"
        )
        if not svcl.same_output_device(current_device, expected):
            logger.info("game audio output device changed externally; keeping the new selection")
            return True

        original_default = saved.original_default_device
        if (
            original_default is not None
            and default_device is not None
            and not svcl.same_output_device(original_default, default_device)
        ):
            logger.info("system default output changed, restoring the system default")
            device = DEFAULT_RENDER_DEVICE
        return svcl.set_app_output(exe_path, device, process)

    def stop(self) -> None:
        worker = self.request_restore()
        if worker is not None and worker is not threading.current_thread():
            # A background switch and the following restore can each call SVCL twice.
            worker.join(timeout=4 * svcl.COMMAND_TIMEOUT_SECONDS + 0.5)


_router = _BackgroundAudioRouter()
atexit.register(_router.stop)

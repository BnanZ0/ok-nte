import atexit
import csv
import io
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from ok import ConfigOption, og
from ok.util.logger import Logger

from src import GAME_EXE
from src.events import communicate
from src.utils import clock

logger = Logger.get_logger(__name__)

SVCL_URL = "https://www.nirsoft.net/utils/sound_volume_command_line.html"
CONFIG_NAME = "Background Audio Routing"
CONF_ENABLE = "Enable Background Audio Routing"
CONF_SVCL_PATH = "SoundVolumeCommandLine Path"
CONF_BACKGROUND_DEVICE = "Background Output Device"
CONF_OPEN_DOWNLOAD_PAGE = "Open SoundVolumeCommandLine Download Page"
DEFAULT_RENDER_DEVICE = "DefaultRenderDevice"
DEFAULT_DEVICE_OPTIONS = [DEFAULT_RENDER_DEVICE]
_COMMAND_TIMEOUT_SECONDS = 5
_WINDOW_ROUTE_CHECK_INTERVAL_SECONDS = 2
_IGNORED_SOUNDDEVICE_HOST_APIS = {"MME", "Windows WDM-KS"}
_SOUND_ITEM_COLUMNS = "Command-LineFriendlyID,ItemID,DeviceState,Direction,ProcessID,Default"
_RESET_PATCH_ATTR = "_background_audio_routing_reset_patched"


def create_background_audio_routing_config_option() -> ConfigOption:
    device_options = _initial_device_options()
    connect_background_audio_router()
    return ConfigOption(
        CONFIG_NAME,
        {
            CONF_ENABLE: False,
            CONF_SVCL_PATH: "",
            CONF_BACKGROUND_DEVICE: device_options[0],
            CONF_OPEN_DOWNLOAD_PAGE: CONF_OPEN_DOWNLOAD_PAGE,
        },
        description=(
            "Optionally route the game to a selected Windows output device while it is in "
            "the background. SoundVolumeCommandLine is not bundled; select your own "
            "downloaded copy."
        ),
        config_description={
            CONF_ENABLE: "Switch game audio output when the game window leaves the foreground",
            CONF_SVCL_PATH: "Select svcl.exe downloaded from NirSoft",
            CONF_BACKGROUND_DEVICE: "Output device used while the game is in the background",
            CONF_OPEN_DOWNLOAD_PAGE: "Open the official NirSoft SoundVolumeCommandLine page",
        },
        config_type={
            CONF_SVCL_PATH: {
                "type": "file_selector",
                "filter": (
                    "SoundVolumeCommandLine (svcl.exe);;Executable Files (*.exe);;All Files (*)"
                ),
                "dialog_title": "Select svcl.exe",
            },
            CONF_BACKGROUND_DEVICE: {
                "type": "drop_down",
                "options": device_options,
            },
            CONF_OPEN_DOWNLOAD_PAGE: {
                "type": "button",
                "text": "Open Download Page",
                "callback": open_svcl_download_page,
            },
        },
        validator=_background_audio_routing_validator(device_options),
    )


def open_svcl_download_page(*_args, **_kwargs) -> None:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(SVCL_URL))
    except Exception as exc:
        logger.error("failed to open SoundVolumeCommandLine download page", exc)
        _alert_error("Failed to open SoundVolumeCommandLine download page")


def discover_output_devices() -> list[str]:
    import sounddevice as sd

    devices = list(DEFAULT_DEVICE_OPTIONS)
    seen = {DEFAULT_RENDER_DEVICE.casefold()}
    try:
        sound_devices = list(sd.query_devices())
        ignored_hostapi_indexes = _ignored_hostapi_indexes(sd.query_hostapis())
        _extend_output_devices(devices, seen, sound_devices, ignored_hostapi_indexes)

        if len(devices) == 1:
            _extend_output_devices(devices, seen, sound_devices)
    except Exception as exc:
        logger.error(f"failed to query output devices using sounddevice: {exc}")
    return devices


def _ignored_hostapi_indexes(hostapis: Any) -> set[int]:
    return {
        i for i, api in enumerate(hostapis) if api.get("name") in _IGNORED_SOUNDDEVICE_HOST_APIS
    }


def _extend_output_devices(
    devices: list[str],
    seen: set[str],
    sound_devices: list[Any],
    ignored_hostapi_indexes: set[int] | None = None,
) -> None:
    for device in sound_devices:
        if not _is_output_sound_device(device, ignored_hostapi_indexes):
            continue
        _append_unique_device(devices, seen, device["name"])


def _is_output_sound_device(device: Any, ignored_hostapi_indexes: set[int] | None) -> bool:
    if device["max_output_channels"] <= 0:
        return False
    return ignored_hostapi_indexes is None or device["hostapi"] not in ignored_hostapi_indexes


def _append_unique_device(devices: list[str], seen: set[str], name: str) -> None:
    key = name.casefold()
    if key in seen:
        return
    seen.add(key)
    devices.append(name)


def _current_process_render_output_device(
    data: list[dict[str, str]],
    process_name: str,
) -> "_RenderOutputDevice | None":
    render_devices = _render_output_devices(data)
    process_ids = _target_process_ids(process_name)
    for record in data:
        if not _is_app_record(record, process_ids):
            continue
        if record.get("direction", "").casefold() != "render":
            continue
        if record.get("devicestate", "").casefold() != "active":
            continue
        device = _resolve_app_render_output_device(
            render_devices,
            _application_command_device_name(record.get("commandlinefriendlyid", "")),
            _sound_item_endpoint_key(record),
        )
        if device is not None:
            return device
    return None


def _is_app_record(
    record: dict[str, str],
    process_ids: set[int],
) -> bool:
    item_type = record.get("type", "").casefold()
    if item_type and item_type != "application":
        return False
    record_pid = _record_process_id(record)
    return bool(process_ids) and record_pid in process_ids


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


def _record_process_id(record: dict[str, str]) -> int | None:
    value = record.get("processid")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _application_command_device_name(command_id: str) -> str:
    parts = command_id.split("\\Application\\", 1)
    return parts[0].strip() if len(parts) == 2 else ""


def _resolve_target_output_device(
    data: Any,
    selected_device: str,
) -> "_RenderOutputDevice | None":
    if selected_device == DEFAULT_RENDER_DEVICE:
        return _RenderOutputDevice(DEFAULT_RENDER_DEVICE, _device_match_key(DEFAULT_RENDER_DEVICE))

    matches = [
        device
        for device in _render_output_devices(data)
        if _sounddevice_name_matches_output(selected_device, device)
    ]
    unique_matches = _unique_output_devices(matches)
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) > 1:
        logger.warning(
            "background audio routing skipped: selected output device is ambiguous in "
            f"svcl sound items: selected={selected_device} candidates={unique_matches}"
        )
        return None

    logger.warning(
        "background audio routing skipped: selected output device was not found in "
        "svcl render devices: "
        f"selected={selected_device} svcl_devices={_render_devices_log(data)}"
    )
    return None


def _resolve_app_render_output_device(
    render_devices: list["_RenderOutputDevice"],
    route_device: str,
    endpoint_key: str,
) -> "_RenderOutputDevice | None":
    if endpoint_key:
        for device in render_devices:
            if device.endpoint_key == endpoint_key:
                return device

    matches = [
        device for device in render_devices if device.match_key == _device_match_key(route_device)
    ]
    unique_matches = _unique_output_devices(matches)
    if len(unique_matches) == 1:
        return unique_matches[0]

    logger.warning(
        "background audio routing skipped: current output device is ambiguous in "
        "svcl render devices: "
        f"current={route_device} candidates={_render_device_records_log(matches)}"
    )
    return None


def _unique_output_devices(
    devices: list["_RenderOutputDevice"],
) -> list["_RenderOutputDevice"]:
    unique: dict[str, _RenderOutputDevice] = {}
    for device in devices:
        unique[device.route_device] = device
    return list(unique.values())


def _resolved_output_devices_match(
    left: "_RenderOutputDevice",
    right: "_RenderOutputDevice",
) -> bool:
    if left.route_device == right.route_device:
        return True
    if left.endpoint_key and right.endpoint_key:
        return left.endpoint_key == right.endpoint_key
    return _device_match_key(left.route_device) == _device_match_key(right.route_device)


def _sounddevice_name_matches_output(
    selected_device: str,
    device: "_RenderOutputDevice",
) -> bool:
    selected_key = _device_match_key(selected_device)
    if not selected_key:
        return False
    names = {
        device.route_device,
        device.controller,
        device.endpoint,
        f"{device.endpoint} ({device.controller})",
    }
    return selected_key in {_device_match_key(name) for name in names if name}


def _render_devices_log(data: Any) -> list[dict[str, str]]:
    return [
        {
            "route_device": device.route_device,
            "endpoint": device.endpoint,
            "controller": device.controller,
        }
        for device in _render_output_devices(data)
    ]


def _render_device_records_log(
    devices: list["_RenderOutputDevice"],
) -> list[dict[str, str]]:
    return [
        {
            "route_device": device.route_device,
            "endpoint": device.endpoint,
            "controller": device.controller,
            "endpoint_key": device.endpoint_key,
        }
        for device in devices
    ]


@dataclass(frozen=True)
class _RenderOutputDevice:
    route_device: str
    match_key: str
    endpoint_key: str = ""
    controller: str = ""
    endpoint: str = ""


def _render_output_devices(data: list[dict[str, str]]) -> list[_RenderOutputDevice]:
    devices = []
    for record in data:
        route_device = record.get("commandlinefriendlyid") or record.get("name", "")
        if not _is_render_endpoint(record, route_device):
            continue
        controller, endpoint = _render_device_parts(route_device)
        match_key = _device_match_key(controller)
        if match_key:
            devices.append(
                _RenderOutputDevice(
                    route_device,
                    match_key,
                    _sound_item_endpoint_key(record),
                    controller,
                    endpoint,
                )
            )
    return devices


def _default_render_output_device(data: list[dict[str, str]]) -> _RenderOutputDevice | None:
    defaults = _render_output_devices(
        [record for record in data if record.get("default", "").casefold() == "render"]
    )
    return defaults[0] if len(defaults) == 1 else None


def audio_route_command(device: str, process_name: str = GAME_EXE) -> list[str]:
    if device != DEFAULT_RENDER_DEVICE and not _is_svcl_render_device_id(device):
        raise ValueError(f"expected a render output device id, got: {device}")
    return ["/SetAppDefault", device, "0", process_name]


def connect_background_audio_router() -> None:
    _router.connect_window_signal()


def restore_background_audio_router() -> None:
    _router.restore_on_exit()


def route_background_audio_for_current_window() -> None:
    _router.route_current_window_state()


@dataclass(frozen=True)
class _RouteRequest:
    device: str
    save_current: bool = False


class _BackgroundAudioRouter:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending_route: _RouteRequest | None = None
        self._requested_device: str | None = None
        self._original_device: str | None = None
        self._background_device: _RenderOutputDevice | None = None
        self._original_default_device: _RenderOutputDevice | None = None
        self._restore_exe_path: str | None = None
        self._restore_needed = False
        self._worker: threading.Thread | None = None
        self._connected = False
        self._bound_exit_event = None
        self._last_visible: bool | None = None
        self.last_mute_check = clock.UNSET

    def on_window(self, visible: bool, *_args) -> None:
        now = clock.now()
        visible_changed = visible != self._last_visible
        recently_checked = now - self.last_mute_check <= _WINDOW_ROUTE_CHECK_INTERVAL_SECONDS
        if not visible_changed and recently_checked:
            return
        self._last_visible = visible
        self.last_mute_check = now
        self.request_route(visible)

    def connect_window_signal(self) -> None:
        self._bind_exit_event()
        with self._lock:
            if self._connected:
                return
            communicate.window.connect(self.on_window)
            self._connected = True

    def request_route(self, visible: bool) -> None:
        self._request_route(visible)

    def route_current_window_state(self) -> None:
        self._bind_exit_event()
        visible = self._current_window_visible()
        if visible is not None:
            self._request_route(visible, enabled=True)

    def _request_route(self, visible: bool, enabled: bool | None = None) -> None:
        self._bind_exit_event()
        config = _routing_config()
        if config is None or not (config.get(CONF_ENABLE, False) if enabled is None else enabled):
            return
        exe_path = config.get(CONF_SVCL_PATH, "")
        if not _is_svcl_path(exe_path):
            logger.warning("background audio routing skipped: svcl.exe is not configured")
            return

        device = self._route_device(visible, config)
        if device is None:
            return
        if not device:
            logger.warning("background audio routing skipped: target output device is empty")
            return

        route = _RouteRequest(device=device, save_current=not visible)
        with self._lock:
            worker_running = self._worker is not None and self._worker.is_alive()
            if route == self._pending_route or device == self._requested_device:
                return
            self._pending_route = route
            self._restore_exe_path = exe_path
            if worker_running:
                return
            self._worker = threading.Thread(
                target=self._run_pending_routes,
                args=(exe_path,),
                name="background_audio_router",
                daemon=True,
            )
            self._worker.start()

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

    def _current_window_visible(self) -> bool | None:
        with self._lock:
            if self._last_visible is not None:
                return self._last_visible
        hwnd_window = getattr(getattr(og, "device_manager", None), "hwnd_window", None)
        visible = getattr(hwnd_window, "visible", None)
        return visible if isinstance(visible, bool) else None

    def _run_pending_routes(self, exe_path: str) -> None:
        while True:
            with self._lock:
                route = self._pending_route
                self._pending_route = None
                if route is None:
                    self._worker = None
                    return
            routed_device = self._switch_process_device(
                exe_path,
                route.device,
                save_current=route.save_current,
            )
            if routed_device:
                self._mark_route_success(route, routed_device)
                # A window event may have been deduplicated against the old device
                # while this command was running. Reconcile after updating its state.
                with self._lock:
                    visible = self._last_visible
                if visible is not None:
                    self.request_route(visible)

    def _route_device(self, visible: bool, config) -> str | None:
        if not visible:
            return config.get(CONF_BACKGROUND_DEVICE)
        with self._lock:
            if not self._restore_needed or self._original_device is None:
                if self._pending_route is not None and self._pending_route.save_current:
                    self._pending_route = None
                return None
            return self._original_device

    def _mark_route_success(self, route: _RouteRequest, routed_device: str) -> None:
        with self._lock:
            if route.save_current:
                self._requested_device = route.device
                original_device = self._original_device or DEFAULT_RENDER_DEVICE
                self._restore_needed = routed_device != original_device
                return
            self._requested_device = None
            self._original_device = None
            self._background_device = None
            self._original_default_device = None
            self._restore_needed = False

    def _switch_process_device(self, exe_path: str, device: str, save_current: bool = False) -> str:
        route_device = (
            self._prepare_background_route(exe_path, device)
            if save_current
            else self._prepare_restore_route(exe_path, device)
        )
        if not route_device:
            return ""

        try:
            command = [exe_path, *audio_route_command(route_device)]
        except ValueError as exc:
            logger.warning(f"background audio routing skipped: {exc}")
            return ""
        logger.info(
            f"route game audio output: tool={Path(exe_path).name} "
            f"device={route_device} process={GAME_EXE}"
        )
        try:
            result = subprocess.run(  # NOSONAR
                command,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            logger.error("failed to route game audio with svcl", exc)
            return ""
        if result.returncode != 0:
            logger.warning(f"svcl audio route failed with exit code {result.returncode}")
            return ""
        return route_device

    def _prepare_background_route(self, exe_path: str, selected_device: str) -> str:
        try:
            data = _export_sound_items(exe_path)
        except Exception as exc:
            logger.warning(f"failed to list svcl sound items before background route: {exc}")
            return ""

        current_render_device = _current_process_render_output_device(data, GAME_EXE)
        if current_render_device is None:
            logger.warning("background audio routing skipped: current output device is unknown")
            return ""

        target_device = _resolve_target_output_device(data, selected_device)
        if target_device is None:
            return ""

        original_device = (
            DEFAULT_RENDER_DEVICE
            if _resolved_output_devices_match(current_render_device, target_device)
            else current_render_device.route_device
        )
        logger.info(
            "save game audio output before background route: "
            f"current={current_render_device.route_device} target={target_device.route_device} "
            f"restore={original_device}"
        )
        with self._lock:
            self._original_device = original_device
            self._background_device = target_device
            default_device = _default_render_output_device(data)
            self._original_default_device = (
                default_device
                if default_device
                and _resolved_output_devices_match(current_render_device, default_device)
                else None
            )
        return target_device.route_device

    def _prepare_restore_route(self, exe_path: str, device: str) -> str:
        with self._lock:
            expected = self._background_device
            original_default = self._original_default_device
        if expected is None:
            return ""
        try:
            data = _export_sound_items(exe_path)
            current = _current_process_render_output_device(data, GAME_EXE)
            default_device = _default_render_output_device(data)
            if expected.route_device == DEFAULT_RENDER_DEVICE:
                if default_device is None:
                    return ""
                expected = default_device
        except Exception as exc:
            logger.warning(f"game audio restore skipped: cannot read current output device: {exc}")
            return ""
        if current is None:
            logger.warning("game audio restore skipped: current output device is unknown")
            return ""
        logger.info(
            "check game audio output before restore: "
            f"current={current.route_device} background={expected.route_device} restore={device} "
            f"system_default={default_device.route_device if default_device else 'unknown'}"
        )
        if not _resolved_output_devices_match(current, expected):
            logger.info("game audio output device changed externally; keeping the new selection")
            with self._lock:
                self._requested_device = None
                self._original_device = None
                self._background_device = None
                self._original_default_device = None
                self._restore_needed = False
            return ""
        if (
            original_default is not None
            and default_device is not None
            and not _resolved_output_devices_match(original_default, default_device)
        ):
            logger.info(
                "system default output changed while backgrounded; restoring system default"
            )
            return DEFAULT_RENDER_DEVICE
        return device

    def restore_on_exit(self) -> None:
        with self._lock:
            worker = self._worker
            self._pending_route = None
        if worker is not None and worker is not threading.current_thread() and worker.is_alive():
            worker.join(timeout=_COMMAND_TIMEOUT_SECONDS + 0.5)

        with self._lock:
            exe_path = self._restore_exe_path or _configured_svcl_path()
            restore_needed = self._restore_needed
            restore_device = self._original_device
        if not restore_needed or not _is_svcl_path(exe_path):
            return
        if not restore_device:
            logger.warning(
                "background audio routing restore skipped: original output device is unknown"
            )
            return
        logger.info(f"restore game audio output on exit: device={restore_device}")
        if self._switch_process_device(exe_path, restore_device):
            with self._lock:
                self._requested_device = None
                self._original_device = None
                self._background_device = None
                self._original_default_device = None
                self._restore_needed = False


def _routing_config():
    global_config = getattr(og, "global_config", None)
    if global_config is None:
        return None
    try:
        config = global_config.get_config(CONFIG_NAME)
    except Exception as exc:
        logger.debug(f"background audio routing config unavailable: {exc}")
        return None
    _patch_reset_to_restore_audio(config)
    return config


def _patch_reset_to_restore_audio(config) -> None:
    if getattr(config, _RESET_PATCH_ATTR, False):
        return
    reset_to_default = getattr(config, "reset_to_default", None)
    if reset_to_default is None:
        return

    def reset_to_default_with_audio_restore(*args, **kwargs):
        was_enabled = bool(config.get(CONF_ENABLE, False))
        reset_to_default(*args, **kwargs)
        if was_enabled and not bool(config.get(CONF_ENABLE, False)):
            restore_background_audio_router()

    config.reset_to_default = reset_to_default_with_audio_restore
    setattr(config, _RESET_PATCH_ATTR, True)


def _ok_exit_event():
    exit_event = getattr(og, "exit_event", None)
    if exit_event is not None:
        return exit_event
    ok_instance = getattr(og, "ok", None)
    return getattr(ok_instance, "exit_event", None)


def _configured_svcl_path() -> str:
    config = _routing_config()
    if not config:
        return ""
    value = config.get(CONF_SVCL_PATH, "")
    return value if isinstance(value, str) else ""


def _initial_device_options() -> list[str]:
    return discover_output_devices()


def _background_audio_routing_validator(device_options: list[str]):
    def validator(key, value):
        if key == CONF_ENABLE:
            if value:
                route_background_audio_for_current_window()
            else:
                restore_background_audio_router()
        if key == CONF_BACKGROUND_DEVICE and value not in device_options:
            return False, "Selected background output device is unavailable"
        if key == CONF_SVCL_PATH and value and not _is_svcl_path(value):
            return False, "Please select svcl.exe"
        return True, None

    return validator


def _is_svcl_path(exe_path: str) -> bool:
    if not exe_path:
        return False
    path = Path(exe_path)
    return path.is_file() and path.name.lower() == "svcl.exe"  # NOSONAR


def _export_sound_items(exe_path: str):
    if not exe_path:
        raise RuntimeError("Please select svcl.exe first")
    if not _is_svcl_path(exe_path):
        raise RuntimeError("Please select a valid svcl.exe file")

    command = [
        exe_path,
        "/scomma",
        "",
        "/Columns",
        _SOUND_ITEM_COLUMNS,
    ]
    result = subprocess.run(  # NOSONAR
        command,
        capture_output=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"svcl failed to list sound items, exit code {result.returncode}")
    data = _parse_sound_items_csv(_decode_sound_items_stdout(result.stdout))
    return data


def _decode_sound_items_stdout(stdout: bytes | str) -> str:
    if isinstance(stdout, str):
        return stdout
    try:
        return stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        return stdout.decode(errors="replace")


def _parse_sound_items_csv(text: str) -> list[dict[str, str]]:
    records = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames:
        reader.fieldnames = [re.sub(r"[\s_\-]+", "", k).casefold() for k in reader.fieldnames]

    for row in reader:
        command_id = row.get("commandlinefriendlyid")
        if not command_id:
            continue
        record = dict(row)
        record.setdefault("type", _sound_item_type(command_id))
        record.setdefault("name", _sound_item_name(command_id))
        records.append(record)
    return records


def _sound_item_type(command_id: str) -> str:
    command_id_lower = command_id.casefold()
    if "\\application\\" in command_id_lower:
        return "Application"
    if "\\device\\" in command_id_lower:
        return "Device"
    if "\\subunit\\" in command_id_lower:
        return "Subunit"
    return ""


def _sound_item_name(command_id: str) -> str:
    for marker in ("\\Application\\", "\\Device\\", "\\Subunit\\"):
        if marker in command_id:
            value = command_id.split(marker, 1)[1]
            if marker == "\\Device\\":
                value = re.sub(r"\\(?:Render|Capture)$", "", value, flags=re.IGNORECASE)
            return value.strip()
    return command_id


def _is_render_endpoint(record: dict[str, str], device_id: str) -> bool:
    device_id_lower = device_id.casefold()
    if not _is_svcl_render_device_id(device_id):
        return False
    if "\\subunit\\" in device_id_lower or record.get("type", "").casefold() == "subunit":
        return False
    return "\\capture" not in device_id_lower and record.get("type", "").casefold() != "application"


def _is_svcl_render_device_id(device: str) -> bool:
    device_lower = device.casefold()
    return "\\device\\" in device_lower and "\\render" in device_lower


def _render_device_parts(device: str) -> tuple[str, str]:
    parts = device.split("\\")
    for index, part in enumerate(parts):
        if part.casefold() == "device" and index > 0 and index + 1 < len(parts):
            return parts[index - 1], parts[index + 1]
    return device, ""


def _sound_item_endpoint_key(record: dict[str, str]) -> str:
    item_id = record.get("itemid", "")
    match = re.search(r"\{0\.0\.[01]\.00000000\}\.\{([^}]+)\}", item_id, re.IGNORECASE)
    return match.group(1).casefold() if match else ""


def _device_match_key(device: str) -> str:
    if not isinstance(device, str):
        return ""
    device = device.casefold()
    device = device.replace("®", "").replace("™", "").replace("©", "")
    device = re.sub(r"\((?:r|tm|c)\)", "", device)
    return "".join(character for character in device if character.isalnum())


def _alert_error(message: str) -> None:
    try:
        from ok.ui.qt.util.Alert import alert_error

        alert_error(message)
    except Exception:
        logger.error(message)


_router = _BackgroundAudioRouter()
atexit.register(restore_background_audio_router)

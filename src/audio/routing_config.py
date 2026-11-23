from collections.abc import Callable
from typing import Any

from ok import ConfigOption
from ok.util.logger import Logger

from src.audio.svcl import DEFAULT_RENDER_DEVICE, is_svcl_path

logger = Logger.get_logger(__name__)

SVCL_URL = "https://www.nirsoft.net/utils/sound_volume_command_line.html"
CONFIG_NAME = "Background Audio Routing"
CONF_ENABLE = "Enable Background Audio Routing"
CONF_SVCL_PATH = "SoundVolumeCommandLine Path"
CONF_BACKGROUND_DEVICE = "Background Output Device"
CONF_OPEN_DOWNLOAD_PAGE = "Open SoundVolumeCommandLine Download Page"
DEFAULT_DEVICE_OPTIONS = [DEFAULT_RENDER_DEVICE]
_IGNORED_SOUNDDEVICE_HOST_APIS = {"MME", "Windows WDM-KS"}
_RESET_PATCH_ATTR = "_background_audio_routing_reset_patched"


def create_config_option(
    on_enable: Callable[[], None], on_disable: Callable[[], None]
) -> ConfigOption:
    device_options = discover_output_devices()
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
        validator=_validator(device_options, on_enable, on_disable),
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


def get_config(global_config, on_disable: Callable[[], None]):
    if global_config is None:
        return None
    try:
        config = global_config.get_config(CONFIG_NAME)
    except Exception as exc:
        logger.debug(f"background audio routing config unavailable: {exc}")
        return None
    _patch_reset_to_restore_audio(config, on_disable)
    return config


def _patch_reset_to_restore_audio(config, on_disable: Callable[[], None]) -> None:
    if getattr(config, _RESET_PATCH_ATTR, False):
        return
    reset_to_default = getattr(config, "reset_to_default", None)
    if reset_to_default is None:
        return

    def reset_to_default_with_audio_restore(*args, **kwargs):
        was_enabled = bool(config.get(CONF_ENABLE, False))
        reset_to_default(*args, **kwargs)
        if was_enabled and not bool(config.get(CONF_ENABLE, False)):
            on_disable()

    config.reset_to_default = reset_to_default_with_audio_restore
    setattr(config, _RESET_PATCH_ATTR, True)


def _validator(
    device_options: list[str], on_enable: Callable[[], None], on_disable: Callable[[], None]
):
    def validator(key, value):
        if key == CONF_ENABLE:
            if value:
                on_enable()
            else:
                on_disable()
        if key == CONF_BACKGROUND_DEVICE and value not in device_options:
            return False, "Selected background output device is unavailable"
        if key == CONF_SVCL_PATH and value and not is_svcl_path(value):
            return False, "Please select svcl.exe"
        return True, None

    return validator


def _alert_error(message: str) -> None:
    try:
        from ok.ui.qt.util.Alert import alert_error

        alert_error(message)
    except Exception:
        logger.error(message)

import csv
import io
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

DEFAULT_RENDER_DEVICE = "DefaultRenderDevice"
COMMAND_TIMEOUT_SECONDS = 5
_SOUND_ITEM_COLUMNS = "Command-LineFriendlyID,ItemID,DeviceState,Direction,ProcessID,Default"


def find_render_session(
    data: list[dict[str, str]], process_ids: set[int]
) -> "tuple[RenderOutputDevice, int] | None":
    render_devices = _render_output_devices(data)
    for record in data:
        if record.get("type", "").casefold() not in ("", "application"):
            continue
        pid = _record_process_id(record)
        if pid is None or pid not in process_ids:
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
            return device, pid
    return None


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


def resolve_output_device(
    data: list[dict[str, str]],
    selected_device: str,
) -> "RenderOutputDevice | None":
    if selected_device == DEFAULT_RENDER_DEVICE:
        return RenderOutputDevice(DEFAULT_RENDER_DEVICE, _device_match_key(DEFAULT_RENDER_DEVICE))

    devices = _render_output_devices(data)
    matches = [
        device for device in devices if _sounddevice_name_matches_output(selected_device, device)
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
        f"selected={selected_device} svcl_devices={_render_device_records_log(devices)}"
    )
    return None


def _resolve_app_render_output_device(
    render_devices: list["RenderOutputDevice"],
    route_device: str,
    endpoint_key: str,
) -> "RenderOutputDevice | None":
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
    devices: list["RenderOutputDevice"],
) -> list["RenderOutputDevice"]:
    unique: dict[str, RenderOutputDevice] = {}
    for device in devices:
        unique[device.route_device] = device
    return list(unique.values())


def same_output_device(
    left: "RenderOutputDevice",
    right: "RenderOutputDevice",
) -> bool:
    if left.route_device == right.route_device:
        return True
    if left.endpoint_key and right.endpoint_key:
        return left.endpoint_key == right.endpoint_key
    return _device_match_key(left.route_device) == _device_match_key(right.route_device)


def _sounddevice_name_matches_output(
    selected_device: str,
    device: "RenderOutputDevice",
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


def _render_device_records_log(
    devices: list["RenderOutputDevice"],
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
class RenderOutputDevice:
    route_device: str
    match_key: str
    endpoint_key: str = ""
    controller: str = ""
    endpoint: str = ""


def _render_output_devices(data: list[dict[str, str]]) -> list[RenderOutputDevice]:
    devices = []
    for record in data:
        route_device = record.get("commandlinefriendlyid") or record.get("name", "")
        if not _is_render_endpoint(record, route_device):
            continue
        controller, endpoint = _render_device_parts(route_device)
        match_key = _device_match_key(controller)
        if match_key:
            devices.append(
                RenderOutputDevice(
                    route_device,
                    match_key,
                    _sound_item_endpoint_key(record),
                    controller,
                    endpoint,
                )
            )
    return devices


def default_output_device(data: list[dict[str, str]]) -> RenderOutputDevice | None:
    defaults = _render_output_devices(
        [record for record in data if record.get("default", "").casefold() == "render"]
    )
    return defaults[0] if len(defaults) == 1 else None


def audio_route_command(device: str, process_name: str | int) -> list[str]:
    if device != DEFAULT_RENDER_DEVICE and not _is_svcl_render_device_id(device):
        raise ValueError(f"expected a render output device id, got: {device}")
    return ["/SetAppDefault", device, "0", str(process_name)]


def is_svcl_path(exe_path: str) -> bool:
    if not exe_path:
        return False
    path = Path(exe_path)
    return path.is_file() and path.name.lower() == "svcl.exe"  # NOSONAR


def list_sound_items(exe_path: str) -> list[dict[str, str]]:
    if not exe_path:
        raise RuntimeError("Please select svcl.exe first")
    if not is_svcl_path(exe_path):
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
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"svcl failed to list sound items, exit code {result.returncode}")
    data = parse_sound_items_csv(_decode_sound_items_stdout(result.stdout))
    return data


def _decode_sound_items_stdout(stdout: bytes | str) -> str:
    if isinstance(stdout, str):
        return stdout
    try:
        return stdout.decode("utf-8-sig")
    except UnicodeDecodeError:
        return stdout.decode(errors="replace")


def parse_sound_items_csv(text: str) -> list[dict[str, str]]:
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


def set_app_output(exe_path: str, route_device: str, process: str | int) -> bool:
    try:
        command = [exe_path, *audio_route_command(route_device, process)]
    except ValueError as exc:
        logger.warning(f"background audio routing skipped: {exc}")
        return False
    logger.info(
        f"route game audio output: tool={Path(exe_path).name} "
        f"device={route_device} process={process}"
    )
    try:
        result = subprocess.run(  # NOSONAR
            command,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        logger.error("failed to route game audio with svcl", exc)
        return False
    if result.returncode != 0:
        logger.warning(f"svcl audio route failed with exit code {result.returncode}")
        return False
    return True

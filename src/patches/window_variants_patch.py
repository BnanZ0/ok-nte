from __future__ import annotations

import os
from functools import wraps

from src.config import WINDOW_VARIANTS

_PATCH_INSTALLED = False

# Original update_pc_device, kept so ensure_capture()/update_capture() can still
# retarget capture at a temporary non-variant window (the local launcher).
_ORIGINAL_UPDATE_PC_DEVICE = None

CLOUD_VARIANT_ID = "pc_cloud"
_VARIANT_BY_ID = {variant["id"]: variant for variant in WINDOW_VARIANTS}
_WINDOW_VARIANT_KEY = "window_variant"

# DeviceManager.clear_devices() (called by ensure_capture) wipes config['preferred'].
# Remember the last explicit choice so a re-enumeration cannot silently fall
# back to the local variant and re-point capture at HTGame.exe / UnrealWindow.
_LAST_PREFERRED_VARIANT = None


def _logger():
    from ok.util.logger import Logger

    return Logger.get_logger(__name__)


def variant_of_device(device):
    """Return the window variant definition carried by a device record."""
    if not device:
        return None
    return _VARIANT_BY_ID.get(device.get(_WINDOW_VARIANT_KEY))


def _as_list(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _is_variant_capture_config(windows_config):
    """Whether the active capture config still describes one of our variants.

    ``DeviceManager.ensure_capture()`` temporarily overwrites the config to
    target a different window (the local launcher) and then calls
    ``update_pc_device()``. Those callers must get the upstream behaviour, not
    the two-variant enumeration, otherwise the launcher window is never found.
    """
    exe = _as_list(windows_config.get("exe"))
    class_names = _as_list(windows_config.get("hwnd_class"))
    if not exe and not class_names:
        # No explicit target: keep the variant enumeration used by Choose Window.
        return True
    return any(
        exe == _as_list(variant["exe"])
        and class_names == _as_list(variant["hwnd_class"])
        for variant in WINDOW_VARIANTS
    )


def is_cloud_window_selected():
    """True when the currently preferred device is the cloud variant.

    Falls back to the device path so the check still works if the device record
    was produced by an unpatched DeviceManager, and to the persisted choice
    while the device list is still empty (start_success fires before the first
    refresh, and the sound thread reads this immediately).
    """
    from ok import og

    manager = getattr(og, "device_manager", None)
    if manager is None:
        return False
    try:
        device = manager.get_preferred_device()
    except Exception:
        device = None
    variant = variant_of_device(device)
    if variant is not None:
        return variant["id"] == CLOUD_VARIANT_ID
    full_path = (device or {}).get("full_path") or ""
    if full_path:
        return os.path.basename(str(full_path)).lower() == "ntecloudgame.exe"

    # device_dict is empty before the first refresh. The last explicit choice
    # wins; otherwise use the persisted preferred imei.
    if _LAST_PREFERRED_VARIANT is not None:
        return _LAST_PREFERRED_VARIANT == CLOUD_VARIANT_ID
    try:
        preferred = (getattr(manager, "config", None) or {}).get("preferred") or ""
    except Exception:
        preferred = ""
    return preferred == CLOUD_VARIANT_ID


def get_selected_variant_id() -> str:
    """Return the variant the user explicitly chose (stable intent).

    Unlike ``is_cloud_window_selected()``, this is NOT about the window that is
    currently being captured. DeviceManager.clear_devices()/ensure_capture()
    temporarily retarget capture at the launcher and wipe config['preferred'],
    and the first device refresh can still expose the previous variant. An
    audio target must stay bound for the whole session, so it is derived from
    the explicit choice instead of the live device record.
    """
    if _LAST_PREFERRED_VARIANT in _VARIANT_BY_ID:
        return _LAST_PREFERRED_VARIANT
    preferred = ""
    try:
        from ok import og

        manager = getattr(og, "device_manager", None)
        preferred = (getattr(manager, "config", None) or {}).get("preferred") or ""
    except Exception:
        preferred = ""
    if preferred in _VARIANT_BY_ID:
        return preferred
    return WINDOW_VARIANTS[0]["id"]


def resolve_audio_capture_spec():
    """Pure, cheap resolver consumed by SoundListener on every loop iteration.

    Keeping this side-effect free (in particular: no logging, no WASAPI work)
    is what lets the listener self-heal even when a variant-switch event is
    lost, arrives early, or reads a stale device record.
    """
    from src.sound_trigger.capture.spec import AudioCaptureSpec

    variant = _VARIANT_BY_ID.get(get_selected_variant_id()) or WINDOW_VARIANTS[0]
    raw = variant.get("audio_capture") or {}
    names = raw.get("process_names") or variant["exe"]
    if isinstance(names, str):
        names = [names]
    return AudioCaptureSpec(
        kind=raw.get("kind", "process_loopback"),
        process_names=tuple(str(name) for name in names if name),
        include_process_tree=bool(raw.get("include_process_tree", True)),
    )


def _apply_variant_settings(manager, device=None):
    """Overwrite config['windows'] with the settings of the selected variant.

    SelectCaptureListView and DeviceManager both read windows_capture_config,
    so changing it before set_preferred_device / do_start makes the capture
    method list and the window matching follow the chosen window.
    """
    windows_config = getattr(manager, "windows_capture_config", None)
    if not windows_config:
        return
    if device is None:
        try:
            device = manager.get_preferred_device()
        except Exception:
            device = None
    variant = variant_of_device(device)
    if variant is None and _LAST_PREFERRED_VARIANT:
        variant = _VARIANT_BY_ID.get(_LAST_PREFERRED_VARIANT)
    if variant is None:
        variant = WINDOW_VARIANTS[0]
    settings = {
        "exe": list(variant["exe"]),
        "hwnd_class": list(variant["hwnd_class"]),
        "capture_method": list(variant["capture_method"]),
    }
    if any(windows_config.get(key) != value for key, value in settings.items()):
        windows_config.update(settings)
        _logger().info(f"window variant {variant['id']}: applied {settings}")


def _update_pc_device_with_variants(self):
    """Replacement for DeviceManager.update_pc_device.

    The upstream implementation only ever produces one pc device. This one
    enumerates each configured window variant independently, so Choose Window
    can list local and cloud side by side even when only one of them is open.
    """
    if getattr(self, "windows_capture_config", None) is None:
        return None

    from ok.util.window import find_hwnd

    windows_config = self.windows_capture_config
    if not _is_variant_capture_config(windows_config):
        # ensure_capture()/update_capture() retargeted capture at another
        # window (e.g. the local launcher). Use the upstream implementation so
        # the temporary config is honoured.
        if _ORIGINAL_UPDATE_PC_DEVICE is None:
            _logger().error(
                "window variants: no original update_pc_device to delegate to"
            )
            return None
        _logger().info(
            "window variants: delegating update_pc_device for a temporary "
            "non-variant capture target"
        )
        return _ORIGINAL_UPDATE_PC_DEVICE(self)

    pc_devices = {}
    for variant in WINDOW_VARIANTS:
        try:
            name, hwnd, full_path, x, y, width, height, hwnds = find_hwnd(
                windows_config.get("title"),
                variant["exe"],
                0,
                0,
                class_name=variant["hwnd_class"],
                selected_hwnd=0,
                top_hwnd_class=windows_config.get("top_hwnd_class"),
            )
        except Exception as error:
            _logger().error(f"find_hwnd failed for window variant {variant['id']}: {error}")
            hwnd, full_path, width, height = 0, None, 0, 0

        imei = variant["id"]
        device = {
            "address": "",
            "imei": imei,
            "device": "windows",
            "model": "",
            "nick": variant["nick"],
            "width": width,
            "height": height,
            "hwnd": variant["nick"],
            "capture": "windows",
            "connected": bool(hwnd),
            "full_path": full_path or "",
            "real_hwnd": hwnd,
            "exe": list(variant["exe"]),
            "resolution": f"{width}x{height}" if width else "",
            _WINDOW_VARIANT_KEY: variant["id"],
        }
        pc_devices[imei] = device

    self._replace_pc_devices(pc_devices)
    _apply_variant_settings(self)
    connected = [(k, v["connected"]) for k, v in pc_devices.items()]
    _logger().debug(f"window variants refreshed: {connected}")

    # Upstream update_pc_device() returns the imei of the window it found, and
    # DeviceManager.ensure_capture() relies on that return value. Returning
    # None here made every ensure_capture() fail with "Cannot find window".
    preferred = self.config.get("preferred")
    if preferred not in pc_devices and _LAST_PREFERRED_VARIANT in pc_devices:
        preferred = _LAST_PREFERRED_VARIANT
    if preferred not in pc_devices:
        preferred = next(
            (imei for imei, device in pc_devices.items() if device.get("connected")),
            None,
        )
    return preferred


def _sync_launcher_task_name():
    """Keep the hidden Start Game task's display name in sync with the variant."""
    try:
        from ok import og

        executor = getattr(og, "executor", None)
        for task in getattr(executor, "onetime_tasks", None) or []:
            if type(task).__name__ == "LauncherTask":
                task.name = task._launcher_display_name()
                return
    except Exception as exc:
        _logger().warning(f"failed to sync launcher task name: {exc}")


# Audio capture targets are deliberately NOT pushed from here. SoundListener
# resolves the user's variant choice itself on every loop iteration, so a
# Choose Window change needs no event, no daemon thread and no cross-thread
# shared hint. Only the launcher task display name still needs the switch.


def _wrap_set_preferred_device(original):
    @wraps(original)
    def set_preferred_device_with_variants(self, imei=None, index=-1):
        global _LAST_PREFERRED_VARIANT
        target = imei
        if index != -1:
            try:
                target = self.get_devices()[index]["imei"]
            except (IndexError, KeyError, TypeError):
                target = imei
        elif target is None:
            target = self.config.get("preferred")
        device = self.device_dict.get(target) if target else None
        if device is None:
            device = self.get_preferred_device()
        variant = variant_of_device(device)
        if variant is not None:
            previous = _LAST_PREFERRED_VARIANT
            _LAST_PREFERRED_VARIANT = variant["id"]
            _apply_variant_settings(self, device)
            if previous != variant["id"]:
                _sync_launcher_task_name()
        # A non-variant device means ensure_capture()/update_capture() is
        # temporarily targeting another window (e.g. the local launcher).
        # Do not clobber that config with the fallback variant.
        return original(self, imei=imei, index=index)

    return set_preferred_device_with_variants


def install_window_variants_patch(config=None):
    """Patch ok's DeviceManager before any OK instance is constructed."""
    global _PATCH_INSTALLED, _ORIGINAL_UPDATE_PC_DEVICE
    if _PATCH_INSTALLED:
        return

    from ok.device.DeviceManager import DeviceManager

    _ORIGINAL_UPDATE_PC_DEVICE = DeviceManager.update_pc_device
    DeviceManager.update_pc_device = _update_pc_device_with_variants
    DeviceManager.set_preferred_device = _wrap_set_preferred_device(
        DeviceManager.set_preferred_device
    )
    _PATCH_INSTALLED = True

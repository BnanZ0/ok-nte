"""Guard rails for the cloud (PC版云异环) window variant.

Design decision: this project will NEVER auto-start the cloud client. Cloud
games are billed by playtime, so a bug that launches (or keeps alive) the
client could cost the user real money. The cloud variant therefore only:

* shows a usage notice when the user selects it (can be permanently dismissed);
* checks for the client window when the user clicks Start / starts a task and,
  if it is missing, shows a dialog and cancels that launch entirely.

Nothing in this module starts a process, queues a task, or opens a network
connection. Activating an already running window (bringing it to the
foreground) is handled by LauncherTask / NTEInteraction and is out of scope.
"""

from __future__ import annotations

import os
import re
from functools import wraps

from ok import Logger, og

from src.utils import clock

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False

_CONFIG_NAME = "cloud_window_notice"
_DONT_SHOW_KEY = "dont_show_again"

CLOUD_EXE = ["NTECloudGame.exe"]
# The class name is generated from the Qt version bundled in NTECloudGame.exe
# (Qt51517 = Qt 5.15.17), not from the machine, so it is stable across devices
# for the same client build. It changes with the client's Qt version and with
# its window state (Icon / OwnDC / OwnDCIcon), so match the family rather than
# one exact value. WGC capture still targets the normal class elsewhere.
# Exclude the client's tool/helper window (`...QWindowToolSaveBits...`) so the
# shared wait/restore step always targets the main window.
CLOUD_WINDOW_CLASS_PATTERN = re.compile(r"^Qt[\d.]+QWindow(?!ToolSaveBits)")

# Avoid stacking the same modal warning when several paths (start button,
# one-time task, scheduled task) all notice the missing window in one run.
_MISSING_DIALOG_COOLDOWN = 30.0
_last_missing_dialog_at = None
_MINIMIZED_NOTICE_COOLDOWN = 30.0
_last_minimized_notice_at = None

NOTICE_TITLE = "PC版云异环 · 使用前须知"
NOTICE_CONTENT = (
    "1. 云游戏需要你手动打开并登录。本工具不会自动启动云·异环客户端，"
    "也不会替你启动任何进程。<br/><br/>"
    "2. 运行期间请保持云游戏窗口在前台。云客户端只接收真实键鼠输入，"
    "切到其它窗口会导致操作失效；程序会在发送输入前尝试把游戏切回前台。"
)

MISSING_TITLE = "云·异环尚未打开"
MISSING_CONTENT = (
    "没有检测到云·异环客户端窗口（NTECloudGame.exe）。<br/><br/>"
    "请手动打开并登录云游戏，然后再点「开始」。<br/><br/>"
    "本次启动已取消：不会启动任何进程，也不会执行任务。"
)


def _tr(text):
    app = getattr(og, "app", None)
    return app.tr(text) if app is not None else text


def _cloud_selected():
    try:
        from src.patches.window_variants_patch import is_cloud_window_selected

        return bool(is_cloud_window_selected())
    except Exception as exc:
        logger.error(f"cloud variant check failed: {exc}")
        return False


def _find_cloud_window_hwnds():
    """Enumerate every window belonging to the cloud client process.

    Deliberately does NOT read ``device_dict['connected']`` or
    ``HwndWindow.exists``: DeviceManager.do_start() clears the hwnd and lets a
    background thread refill it, so both flags can be transiently False even
    while the game has been open for hours (see the 2026-11-07 startup log).

    The client's Qt class changes with its state (``Qt51517QWindowIcon`` when
    normal, ``Qt51517QWindowOwnDC``/``...OwnDCIcon`` when minimized). The
    upstream ``find_hwnd`` also drops windows whose client rect is 0x0, which
    is exactly a minimized window. Enumerate by exe + class family instead.
    """
    try:
        import win32gui
        from ok.util.window import get_exe_by_hwnd

        exe_names = {name.lower() for name in CLOUD_EXE}
        matches = []

        def visit(hwnd, _):
            try:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowEnabled(hwnd):
                    return True
                if not CLOUD_WINDOW_CLASS_PATTERN.match(
                    win32gui.GetClassName(hwnd)
                ):
                    return True
                _, full_path, _ = get_exe_by_hwnd(hwnd)
                if os.path.basename(str(full_path)).lower() in exe_names:
                    matches.append(hwnd)
            except Exception:
                return True
            return True

        win32gui.EnumWindows(visit, None)
        return matches
    except Exception as exc:
        # Fail closed: a broken enumeration must never allow the launch.
        logger.error(f"cloud window check failed, assuming not running: {exc}")
        return []


def cloud_window_available():
    """True when a real cloud client window exists right now (minimized counts)."""
    return bool(_find_cloud_window_hwnds())


def _is_window_iconic(hwnd):
    try:
        import win32gui

        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def cloud_window_minimized():
    """True when any cloud client window is currently minimized."""
    try:
        return any(_is_window_iconic(hwnd) for hwnd in _find_cloud_window_hwnds())
    except Exception as exc:
        logger.error(f"cloud minimized check failed: {exc}")
        return False


def _restore_cloud_windows(hwnds):
    """Restore minimized cloud windows before StartController.do_start().

    This runs from the Start guard, which is earlier than LauncherTask. Doing
    it here lets HwndWindow bind and the first WGC attempt succeed instead of
    waiting for the task-level recovery.
    """
    restored = []
    try:
        import win32con
        import win32gui

        for hwnd in hwnds:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                restored.append(hwnd)
    except Exception as exc:
        logger.warning(f"failed to restore cloud window: {exc}")
        return
    if restored:
        logger.info(f"Cloud window restored before capture setup: {restored}")


def _log_cloud_window_state(hwnds):
    """Log what the cloud guard actually sees; useful for start-time reports."""
    if not hwnds:
        logger.info("Cloud window check: no NTECloudGame.exe window")
        return
    try:
        import win32gui
    except Exception as exc:
        logger.info(f"Cloud window check: hwnds={list(hwnds)} (details unavailable: {exc})")
        return

    details = []
    for hwnd in hwnds:
        try:
            details.append(
                f"hwnd={hwnd} class={win32gui.GetClassName(hwnd)!r} "
                f"iconic={bool(win32gui.IsIconic(hwnd))} "
                f"visible={bool(win32gui.IsWindowVisible(hwnd))} "
                f"title={win32gui.GetWindowText(hwnd)!r}"
            )
        except Exception as exc:
            details.append(f"hwnd={hwnd} error={exc}")
    logger.info("Cloud window check: " + " | ".join(details))


def show_cloud_minimized_notice():
    """Non-blocking reminder; the launch continues and restores the window."""
    global _last_minimized_notice_at

    now = clock.now()
    if (
        _last_minimized_notice_at is not None
        and now - _last_minimized_notice_at < _MINIMIZED_NOTICE_COOLDOWN
    ):
        return False
    _last_minimized_notice_at = now
    logger.warning(
        "Cloud window is minimized; restoring it before capture setup"
    )
    try:
        from src.events import communicate

        communicate.info_bar_requested.emit(
            title=_tr("云游戏窗口"),
            content=_tr(
                "云·异环当前已最小化, 程序会尝试恢复并切到前台; "
                "如果没有恢复, 请手动还原窗口。"
            ),
            success=False,
            duration=5000,
        )
        return True
    except Exception as exc:
        logger.error(f"show cloud minimized notice failed: {exc}")
        return False


def _config():
    from ok.util.config import Config

    # Config writes itself to configs/<name>.json on __setitem__.
    return Config(_CONFIG_NAME, {_DONT_SHOW_KEY: False})


def notice_dismissed():
    try:
        return bool(_config().get(_DONT_SHOW_KEY, False))
    except Exception as exc:
        logger.error(f"read cloud notice preference failed: {exc}")
        return False


def set_notice_dismissed(value=True):
    try:
        config = _config()
        config[_DONT_SHOW_KEY] = bool(value)
        config.save_file()
        return True
    except Exception as exc:
        logger.error(f"save cloud notice preference failed: {exc}")
        return False


def _gui_thread():
    """Return the Qt application only when a real GUI is running."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return None
        from PySide6.QtCore import QThread

        if QThread.currentThread() != app.thread():
            return None
        return app
    except Exception:
        return None


def show_cloud_usage_notice():
    """Dialog A: usage notice with a permanent "don't show again" checkbox.

    Only called from the GUI thread (window selection happens there). Returns
    True when the dialog was actually shown.
    """
    if _gui_thread() is None:
        return False
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

        from src.ui.foundation.dialogs import MessageBoxBase

        # MessageBoxBase derives from MaskDialogBase, whose __init__ calls
        # parent.width()/height() unconditionally. It must have a real parent.
        parent = getattr(og, "main_window", None) or QApplication.activeWindow()
        if parent is None:
            logger.warning("Cloud usage notice skipped: no parent window")
            return False
        dialog = MessageBoxBase(parent)
        dialog.widget.setMinimumWidth(480)

        label = QLabel(_tr(NOTICE_CONTENT), dialog)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        dialog.viewLayout.addWidget(label)

        dont_show = QCheckBox(_tr("不再提示"), dialog)
        dialog.viewLayout.addWidget(dont_show)

        dialog.yesButton.setText(_tr("知道了"))
        dialog.cancelButton.hide()
        dialog.exec()

        if dont_show.isChecked():
            set_notice_dismissed(True)
            logger.info("Cloud usage notice dismissed permanently")
        return True
    except Exception as exc:
        logger.error(f"show cloud usage notice failed: {exc}")
        return False


def show_cloud_missing_dialog():
    """Dialog B: tell the user to open the cloud game manually.

    ``show_dialog_and_wait`` is thread safe, so this works both from the GUI
    thread (Start button) and from TaskExecutor (scheduled / CLI tasks).
    Returns True when a dialog was shown; callers must cancel the launch
    regardless, even when there is no GUI at all.
    """
    global _last_missing_dialog_at

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        QApplication = None
    if QApplication is None or QApplication.instance() is None:
        logger.warning("Cloud game window is missing (headless: no dialog shown)")
        return False

    now = clock.now()
    if (
        _last_missing_dialog_at is not None
        and now - _last_missing_dialog_at < _MISSING_DIALOG_COOLDOWN
    ):
        logger.info("Cloud missing-window dialog suppressed by cooldown")
        return False
    _last_missing_dialog_at = now

    try:
        from src.ui.foundation.dialogs import show_dialog_and_wait

        show_dialog_and_wait(
            _tr(MISSING_TITLE),
            _tr(MISSING_CONTENT),
            copyable=False,
            rich_text=True,
            hide_cancel=True,
        )
        return True
    except Exception as exc:
        logger.error(f"show cloud missing-window dialog failed: {exc}")
        return False


def should_block_cloud_launch():
    """True only when no cloud client window exists at all.

    A minimized window is open and billed. It does not satisfy the real-input
    foreground requirement yet, so restore it here (before ``do_start``) and
    remind the user; the launcher/input layer keeps it foreground afterwards.
    Do not cancel the launch.
    """
    if not _cloud_selected():
        return False
    hwnds = _find_cloud_window_hwnds()
    _log_cloud_window_state(hwnds)
    if not hwnds:
        show_cloud_missing_dialog()
        logger.warning(
            "Cloud game window not found; launch cancelled (no process started, "
            "no task queued)"
        )
        return True
    if any(_is_window_iconic(hwnd) for hwnd in hwnds):
        _restore_cloud_windows(hwnds)
        show_cloud_minimized_notice()
    return False


def _wrap_start(original):
    @wraps(original)
    def start_with_cloud_guard(self, task=None, exit_after=False):
        if should_block_cloud_launch():
            return None
        return original(self, task, exit_after)

    return start_with_cloud_guard


def _wrap_set_preferred_device(original):
    @wraps(original)
    def set_preferred_with_notice(self, imei=None, index=-1):
        was_cloud = _cloud_selected()
        result = original(self, imei=imei, index=index)
        # Only a real transition into the cloud variant shows the notice.
        # StartTab.update_capture() calls setCurrentRow() on every device
        # refresh, which re-enters this hook with an explicit index; comparing
        # before/after keeps those refreshes from re-popping the dialog.
        # Internal no-arg calls from do_start/do_refresh stay quiet as well.
        user_initiated = imei is not None or index != -1
        is_cloud = _cloud_selected()
        if (
            user_initiated
            and is_cloud
            and not was_cloud
            and not notice_dismissed()
        ):
            show_cloud_usage_notice()
        return result

    return set_preferred_with_notice


def install_cloud_launch_guard():
    """Patch the single start entry point and window selection.

    Must run AFTER install_window_variants_patch so the outer selection hook
    sees the variant that the inner hook just applied.
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.core.start_controller import StartController
    from ok.device.DeviceManager import DeviceManager

    StartController.start = _wrap_start(StartController.start)
    DeviceManager.set_preferred_device = _wrap_set_preferred_device(
        DeviceManager.set_preferred_device
    )
    _PATCH_INSTALLED = True
    logger.info("Cloud launch guard installed (cloud client is never auto-started)")

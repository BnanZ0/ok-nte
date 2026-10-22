"""Windows screen saver inspection and dismissal helpers."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from ok.util.logger import Logger

from src.utils import clock

SPI_GETSCREENSAVERRUNNING = 0x0072
DESKTOP_SWITCHDESKTOP = 0x0100
CURSOR_NUDGE_PIXELS = 50
CURSOR_CHECK_DELAY_SECONDS = 1
DISMISS_TIMEOUT_SECONDS = 10

logger = Logger.get_logger(__name__)


def _windows_api():
    """Load the User32 and Kernel32 functions with 64-bit-safe signatures."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.SystemParametersInfoW.argtypes = (
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        wintypes.UINT,
    )
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.GetThreadDesktop.argtypes = (wintypes.DWORD,)
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = (wintypes.INT, wintypes.INT)
    user32.SetCursorPos.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return user32, kernel32


def _screensaver_state() -> bool | None:
    """Return the screen saver state, or None when Windows cannot provide it."""
    try:
        user32, _kernel32 = _windows_api()
        running = wintypes.BOOL()
        if user32.SystemParametersInfoW(SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(running), 0):
            return bool(running.value)
        error = ctypes.get_last_error()
    except OSError as exception:
        logger.warning(f"Could not query screen saver state: {exception}")
        return None

    logger.warning(f"Could not query screen saver state: error={error}")
    return None


def is_screensaver_running() -> bool:
    """Return whether a screen saver is currently running in this session."""
    return _screensaver_state() is True


def _dismiss_on_input_desktop() -> bool:
    """Nudge the cursor on the input desktop and confirm screen saver dismissal."""
    user32 = None
    old_desktop = None
    input_desktop = None
    switched_to_input = False
    try:
        user32, kernel32 = _windows_api()
        old_desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if not old_desktop:
            logger.warning(f"Could not get current thread desktop: error={ctypes.get_last_error()}")
            return False

        input_desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not input_desktop:
            logger.warning(f"Could not open input desktop: error={ctypes.get_last_error()}")
            return False
        if not user32.SetThreadDesktop(input_desktop):
            logger.warning(f"Could not switch to input desktop: error={ctypes.get_last_error()}")
            return False
        switched_to_input = True

        position = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(position)):
            logger.warning(f"Could not get cursor position: error={ctypes.get_last_error()}")
            return False

        timer = clock.timer(timeout=DISMISS_TIMEOUT_SECONDS)
        move_positive = True
        while not timer.expired():
            target_x = position.x + CURSOR_NUDGE_PIXELS if move_positive else position.x
            destination = "move-positive" if move_positive else "move-negative"
            if not user32.SetCursorPos(target_x, position.y):
                logger.warning(
                    f"Could not move cursor to dismiss screen saver ({destination}): "
                    f"error={ctypes.get_last_error()}"
                )
                return False
            time.sleep(CURSOR_CHECK_DELAY_SECONDS)

            state = _screensaver_state()
            if state is False:
                if not move_positive:
                    return True
                if not user32.SetThreadDesktop(old_desktop):
                    logger.warning(
                        f"Could not restore original desktop: error={ctypes.get_last_error()}"
                    )
                    return False
                switched_to_input = False
                if not user32.SetCursorPos(position.x, position.y):
                    logger.warning(
                        f"Could not restore cursor position: error={ctypes.get_last_error()}"
                    )
                    return False
                return True
            if state is not True:
                return False
            move_positive = not move_positive

        logger.warning(f"Screen saver did not exit within {DISMISS_TIMEOUT_SECONDS}s")
        return False
    except OSError as exception:
        logger.warning(f"Could not dismiss screen saver on input desktop: {exception}")
        return False
    finally:
        if switched_to_input and old_desktop and user32:
            if not user32.SetThreadDesktop(old_desktop):
                logger.warning(
                    f"Could not restore original desktop: error={ctypes.get_last_error()}"
                )
        if input_desktop and user32:
            user32.CloseDesktop(input_desktop)


def dismiss_screensaver() -> bool:
    """Dismiss a running screen saver and return whether its exit was confirmed."""
    if _screensaver_state() is not True:
        return False

    result = [False]

    def run() -> None:
        result[0] = _dismiss_on_input_desktop()

    worker = threading.Thread(target=run, name="dismiss-screensaver", daemon=True)
    worker.start()
    worker.join(timeout=DISMISS_TIMEOUT_SECONDS + 1)
    if worker.is_alive():
        logger.warning("Screen saver dismissal worker did not finish in time")
        return False
    if result[0]:
        logger.info("Dismissed screen saver")
    return result[0]

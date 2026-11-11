"""Request elevation before the current app exits.

ok's stock ``MainWindow.restart_admin()`` starts a daemon thread that calls
``ShellExecuteW(..., "runas")`` and immediately calls ``app.quit()``. The UAC
prompt is owned by the shell, so it can appear only after the app window is
already gone; cancelling it leaves the user with no running app. This patch
performs the elevation request synchronously (``ShellExecuteW`` blocks while
the UAC prompt is visible) and quits only after the elevated instance was
actually launched.

Nothing here starts a game process or changes task behaviour. It only fixes
the ordering of the existing "restart as administrator" flow.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

from ok import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False

# ShellExecuteW returns ERROR_CANCELLED when the user dismisses UAC. The value
# is greater than 32, so the usual "> 32 means success" check is not enough.
_ERROR_CANCELLED = 1223

_ADMIN_MESSAGE = (
    "PC version requires admin privileges, Please restart this app with admin privileges!"
)
_RESTART_FAILED_MESSAGE = (
    "Failed to restart with administrator privileges. "
    "Please close this app and start it again as administrator."
)


def elevation_succeeded(result: int) -> bool:
    """Whether ShellExecuteW actually launched the elevated instance."""
    return result > 32 and result != _ERROR_CANCELLED


def _shell_execute(executable: str, args: str, cwd: str, show_command: int) -> int:
    return int(
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, args, cwd, show_command
        )
    )


def launch_elevated(*, argv=None, executable=None, cwd=None, show_command=1) -> int:
    """Launch this app elevated and return ShellExecuteW's result.

    ``ShellExecuteW`` blocks until the user answers the UAC prompt, which is
    exactly what keeps the current window alive while the prompt is visible.
    """
    argv = list(sys.argv) if argv is None else list(argv)
    executable = sys.executable if executable is None else executable
    cwd = os.getcwd() if cwd is None else cwd
    args = subprocess.list2cmdline(argv)
    # Never log absolute paths: sys.executable, argv and cwd are user-local.
    logger.info(
        f"restart_as_admin: requesting elevation "
        f"exe={os.path.basename(executable)} argv_count={len(argv)}"
    )
    return _shell_execute(executable, args, cwd, show_command)


def finish_restart(app, result: int, show_error) -> bool:
    """Quit only after the elevated instance started; otherwise keep running."""
    if elevation_succeeded(result):
        logger.info(
            f"restart_as_admin: elevated instance launched "
            f"(ShellExecuteW={result}); quitting current app"
        )
        app.quit()
        return True
    # Keep this instance alive: quitting before/while the shell prompt is
    # answered is what made the prompt appear after the app closed.
    logger.error(
        f"restart_as_admin: elevation cancelled or failed "
        f"(ShellExecuteW={result}); keeping this instance running"
    )
    show_error()
    return False


def install_admin_restart_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.MainWindow import MainWindow
    from PySide6.QtCore import QCoreApplication
    from qfluentwidgets import MessageBox

    def restart_admin(self):
        w = MessageBox(
            QCoreApplication.translate("app", "Alert"),
            QCoreApplication.translate("StartController", _ADMIN_MESSAGE),
            self.window(),
        )
        if not w.exec():
            logger.info("restart_admin cancelled by user")
            return

        logger.info("restart_admin Yes button is pressed")
        try:
            result = launch_elevated()
        except Exception as exc:
            logger.error(f"restart_as_admin failed to request elevation: {exc}")
            result = 0

        def show_error():
            MessageBox(
                QCoreApplication.translate("app", "Alert"),
                QCoreApplication.translate("StartController", _RESTART_FAILED_MESSAGE),
                self.window(),
            ).exec()

        finish_restart(self.app, result, show_error)

    MainWindow.restart_admin = restart_admin
    _PATCH_INSTALLED = True

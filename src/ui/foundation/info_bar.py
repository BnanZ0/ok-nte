"""Shared UI feedback and a subscriber for project notification events."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition

from src.events import communicate


def show_bar(
    parent: QWidget,
    title: str,
    content: str,
    success: bool = True,
    *,
    duration: int | None = None,
) -> None:
    """Show feedback on the UI thread; a negative duration keeps it visible."""
    if duration is None:
        duration = 2500 if success else 3500
    show = InfoBar.success if success else InfoBar.error
    show(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP,
        duration=duration,
        parent=parent,
    )


def install_info_bar_handler(parent: QWidget) -> None:
    """Subscribe once; the existing event bus dispatches callbacks on the UI thread."""
    if getattr(parent, "_info_bar_handler", None) is not None:
        return
    active = True

    def handle(title: str, content: str, success: bool = False, duration: int = -1):
        if active:
            show_bar(parent, title, content, success, duration=duration)

    def disconnect(*_):
        nonlocal active
        active = False
        communicate.info_bar_requested.disconnect(handle)

    communicate.info_bar_requested.connect(handle)
    parent.destroyed.connect(disconnect)
    parent._info_bar_handler = handle

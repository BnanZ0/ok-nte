"""Shared transient feedback for Qt tabs and dialogs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition


def show_bar(parent: QWidget, title: str, content: str, success: bool = True) -> None:
    show = InfoBar.success if success else InfoBar.error
    show(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP,
        duration=2500 if success else 3500,
        parent=parent,
    )

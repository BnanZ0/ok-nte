"""Shared command bar surface and native toolbar styling."""

from ok.ui.qt.kit import Card, theme
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolBar


class CommandBarCard(Card):
    """Kit card with consistent spacing for a command bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)


class ThemedToolBar(QToolBar):
    """Keep native toolbar action text aligned with the active kit theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._refresh_action_style()
        theme.changed.connect(self._refresh_action_style)

    def _refresh_action_style(self):
        disabled = theme.color("text_disabled").name()
        separator = theme.color("separator").name()
        self.setStyleSheet(
            f"QToolButton:disabled {{ color: {disabled}; }}"
            f"QToolBar::separator {{ background: {separator}; width: 1px; margin: 6px 4px; }}"
        )

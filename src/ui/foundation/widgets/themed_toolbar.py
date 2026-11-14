"""Shared command bar surface and native toolbar styling."""

from ok.ui.qt.kit import Card, theme
from ok.ui.qt.kit.icons import tinted_icon
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QActionEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolBar


class CommandBarCard(Card):
    """Kit card with consistent spacing for a command bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)


class ThemedToolBar(QToolBar):
    """Keep toolbar action text and icons aligned with the active kit theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._action_icons = {}
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._refresh_action_style()
        theme.changed.connect(self._refresh_action_style)

    def actionEvent(self, event: QActionEvent):
        super().actionEvent(event)
        action = event.action()
        if event.type() == QEvent.Type.ActionAdded and action.isCheckable():
            self._action_icons[action] = action.icon()
            action.toggled.connect(self._on_action_toggled)
            self._refresh_action_icon(action, action.isChecked())
        elif event.type() == QEvent.Type.ActionRemoved:
            self._action_icons.pop(action, None)

    def _on_action_toggled(self, checked: bool):
        action = self.sender()
        if action in self._action_icons:
            self._refresh_action_icon(action, checked)

    def _refresh_action_icon(self, action, checked: bool):
        icon = self._action_icons[action]
        if not icon.isNull():
            action.setIcon(tinted_icon(icon, "on_accent" if checked else "text"))

    def _refresh_action_style(self):
        disabled = theme.color("text_disabled").name()
        on_accent = theme.color("on_accent").name()
        separator = theme.color("separator").name()
        self.setStyleSheet(
            f"QToolButton:disabled {{ color: {disabled}; }}"
            f"QToolButton:enabled:checked {{ color: {on_accent}; }}"
            f"QToolBar::separator {{ background: {separator}; width: 1px; margin: 6px 4px; }}"
        )
        for action in self._action_icons:
            self._refresh_action_icon(action, action.isChecked())

"""Shared card layouts and state indicators built on the kit."""

from ok.ui.qt.kit import Card, Label, icon, theme
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from src.ui.foundation.widgets.separator import Separator


class SectionCard(Card):
    """A kit card with an inset header and a full-width separator."""

    def __init__(self, title="", *, icon_name=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        header_widget = QFrame(self)
        header_widget.setMinimumHeight(44)
        self.header = QHBoxLayout(header_widget)
        self.header.setContentsMargins(14, 6, 14, 6)
        self.header.setSpacing(10)
        if icon_name:
            self._header_icon = QLabel(self)
            self._header_icon.setFixedSize(18, 18)
            self._icon = icon(icon_name)
            self.header.addWidget(self._header_icon)
        if title:
            self.header.addWidget(Label(title, "strong", self), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(header_widget)

        self._separator = Separator(self)
        layout.addWidget(self._separator)
        self.body_widget = QFrame(self)
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(14, 12, 14, 14)
        self.body.setSpacing(10)
        layout.addWidget(self.body_widget, 1)
        self._refresh_header()
        theme.changed.connect(self._refresh_header)

    def set_body_visible(self, visible):
        self._separator.setVisible(visible)
        self.body_widget.setVisible(visible)

    def _refresh_header(self):
        if hasattr(self, "_header_icon"):
            self._header_icon.setPixmap(self._icon.pixmap(self._header_icon.size()))


class BorderCardWidget(Card):
    def __init__(self, parent=None, border_width: float = 1.0, border_color=None):
        super().__init__(parent)
        self._border_width = max(0.0, border_width)
        self._border_color = None
        self.setBorderColor(border_color)

    def borderWidth(self) -> float:
        return self._border_width

    def setBorderWidth(self, width: float):
        self._border_width = max(0.0, width)
        self.update()

    def borderColor(self) -> QColor | None:
        return QColor(self._border_color) if self._border_color is not None else None

    def setBorderColor(self, color):
        value = None if color is None else QColor(color)
        if value is not None and not value.isValid():
            raise ValueError(f"Invalid border color: {color!r}")
        self._border_color = value
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._border_color is None or self._border_width <= 0:
            return
        width = min(self._border_width, self.width(), self.height())
        inset = width / 2
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = max(0.0, 12 - inset)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._border_color, width))
        painter.drawRoundedRect(rect, radius, radius)

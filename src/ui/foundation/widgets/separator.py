from math import ceil

from ok.ui.qt.kit import theme
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget


def paint_separator(widget, rect, *, horizontal=True):
    """Paint the same pixel-aligned rule on a widget or inside another control."""
    line = QRectF(rect)
    dpr = widget.devicePixelRatioF()
    stroke = ceil(dpr) / dpr
    if horizontal:
        line.setTop((line.top() + line.bottom() - stroke) / 2)
        line.setHeight(stroke)
    else:
        line.setLeft((line.left() + line.right() - stroke) / 2)
        line.setWidth(stroke)
    QPainter(widget).fillRect(line, theme.color("separator"))


class Separator(QWidget):
    """Navigation-colored rule with its stroke rounded up to a device pixel."""

    def __init__(self, parent=None, *, orientation=Qt.Orientation.Horizontal, spacing=0):
        super().__init__(parent)
        self._horizontal = orientation == Qt.Orientation.Horizontal
        thickness = 2 * spacing + 2
        if self._horizontal:
            self.setFixedHeight(thickness)
        else:
            self.setFixedWidth(thickness)
        theme.changed.connect(self.update)

    def paintEvent(self, event):
        paint_separator(self, self.rect(), horizontal=self._horizontal)

"""Kit table with column dividers in its header."""

from ok.ui.qt.kit import Table, theme
from ok.ui.qt.kit.table import _TableHeader
from PySide6.QtGui import QPainter


class ThemedTable(Table):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setHorizontalHeader(_DividedTableHeader(self))


class _DividedTableHeader(_TableHeader):
    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        boundary = rect.right() + 1
        if boundary >= self.viewport().width() or self.logicalIndexAt(boundary) < 0:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(theme.color("border"))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.restore()

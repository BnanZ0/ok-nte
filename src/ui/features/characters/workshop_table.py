"""Workshop table details painted without overriding the table's stylesheet."""

from ok.ui.qt.kit import theme
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem


class WorkshopItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        if option.state & QStyle.StateFlag.State_Selected:
            surface = theme.color("surface")
            accent = theme.accent()
            selected = QColor.fromRgbF(
                surface.redF() * 0.86 + accent.redF() * 0.14,
                surface.greenF() * 0.86 + accent.greenF() * 0.14,
                surface.blueF() * 0.86 + accent.blueF() * 0.14,
            )
            option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
            option.features &= ~QStyleOptionViewItem.ViewItemFeature.Alternate
            option.backgroundBrush = QBrush(selected)
        option.widget.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget
        )


class WorkshopHeader(QHeaderView):
    """Keep the workshop's joined header cells and rounded outer top corners."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

    def updateGeometries(self):
        super().updateGeometries()
        # The kit's general item-view frame belongs to the table, not this header.
        self.viewport().setGeometry(self.rect())

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        first = self.visualIndex(logical_index) == 0
        last = self.visualIndex(logical_index) == self.count() - 1
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        bounds = QRectF(rect)
        path.addRoundedRect(bounds, 9, 9)
        # Only the two outside top corners are rounded.
        path.addRect(bounds.adjusted(0, 9, 0, 0))
        path.addRect(bounds.adjusted(9 if first else 0, 0, -9 if last else 0, 0))
        painter.fillPath(path.simplified(), theme.color("surface_alt"))
        painter.setPen(QPen(theme.color("border"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if not last:
            painter.drawLine(rect.topRight(), rect.bottomRight())
        font = self.font()
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.color("text_secondary"))
        text = str(self.model().headerData(logical_index, self.orientation()) or "")
        text = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, rect.width() - 24)
        )
        painter.drawText(rect.adjusted(12, 0, -12, 0), self.defaultAlignment(), text)
        painter.restore()

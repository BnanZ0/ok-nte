"""Shared row rendering for the application's collection lists."""

from ok.ui.qt.kit import theme
from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip

COLLECTION_STATUS_ROLE = Qt.ItemDataRole.UserRole + 1


class CollectionItemDelegate(QStyledItemDelegate):
    """Keep native item interaction while painting consistent collection rows."""

    def __init__(self, parent, *, tree_groups=False):
        super().__init__(parent)
        self._tree_groups = tree_groups

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setWidth(0)
        size.setHeight(36)
        return size

    def paint(self, painter, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        selected = bool(item.state & QStyle.StateFlag.State_Selected)
        hovered = bool(item.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(item.state & QStyle.StateFlag.State_Enabled)
        group = self._tree_groups and index.model().hasChildren(index)
        row = item.rect.adjusted(4, 2, -4, -2)
        painter.save()
        painter.setClipRect(item.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            fill = QColor(255, 255, 255, 20) if theme.is_dark() else QColor(0, 0, 0, 10)
            painter.setBrush(fill)
            painter.drawRoundedRect(row, 6, 6)
            painter.setBrush(theme.accent())
            painter.drawRoundedRect(QRectF(row.left(), row.center().y() - 8, 3, 16), 1.5, 1.5)
        elif hovered and enabled:
            painter.setBrush(theme.color("control_hover"))
            painter.drawRoundedRect(row, 6, 6)

        text = row.adjusted(14, 0, -12, 0)
        status = index.data(COLLECTION_STATUS_ROLE)
        if status:
            status_font = QFont(item.font)
            status_font.setPixelSize(11)
            status_font.setWeight(QFont.Weight.Medium)
            width = QFontMetrics(status_font).horizontalAdvance(status) + 10
            badge = QRect(row.right() - 7 - width, row.center().y() - 9, width, 20)
            if badge.left() - 6 > text.left():
                painter.setPen(QPen(theme.color("border_strong"), 1))
                painter.setBrush(theme.color("control"))
                painter.drawRoundedRect(QRectF(badge).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                painter.setFont(status_font)
                painter.setPen(theme.color("text_secondary" if enabled else "text_disabled"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, status)
                text.setRight(badge.left() - 7)

        font = QFont(item.font)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold if selected or group else QFont.Weight.Normal)
        painter.setFont(font)
        color = "text_secondary" if group and not selected else "text"
        if not enabled:
            color = "text_disabled"
        painter.setPen(theme.color(color))
        title = QFontMetrics(font).elidedText(item.text, Qt.TextElideMode.ElideRight, text.width())
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter, title)

        view = self.parent()
        if (
            enabled
            and item.state & QStyle.StateFlag.State_HasFocus
            and view.hasFocus()
            and view.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
        ):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.accent(), 1))
            painter.drawRoundedRect(QRectF(row).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        painter.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            text = index.data(Qt.ItemDataRole.ToolTipRole) or index.data(
                Qt.ItemDataRole.DisplayRole
            )
            if text:
                status = index.data(COLLECTION_STATUS_ROLE)
                tooltip = f"{text}\n{status}" if status else str(text)
                QToolTip.showText(event.globalPos(), tooltip, view)
                return True
        return super().helpEvent(event, view, option, index)


def style_flat_collection(view):
    """Opt a main collection list into the shared rows and its parent surface."""
    view.setMouseTracking(True)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setItemDelegate(CollectionItemDelegate(view))
    view.setStyleSheet("QListWidget { background: transparent; border: 0; padding: 4px 0; }")

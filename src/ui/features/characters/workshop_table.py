"""Themed catalog table for the community workshop."""

from ok.ui.qt.kit import theme
from PySide6.QtCore import QEvent, QPointF, QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import (
    QHeaderView,
    QScrollBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QTableWidget,
)

FRAME_WIDTH = 1
FRAME_RADIUS = 10
INNER_RADIUS = FRAME_RADIUS - FRAME_WIDTH


class WorkshopTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered_row = -1
        self._scrollbar_overlay = WorkshopScrollBar(self)
        self._scrollbar_overlay.hide()
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll = self.verticalScrollBar()
        scroll.valueChanged.connect(self._scrollbar_overlay.setValue)
        self._scrollbar_overlay.valueChanged.connect(scroll.setValue)
        self.setHorizontalHeader(WorkshopHeader(self))
        theme.changed.connect(self._apply_theme)
        self._apply_theme()
        self.setItemDelegate(WorkshopItemDelegate(self))

    def _apply_theme(self):
        surface = theme.color("surface").name()
        border = theme.color("border").name()
        text = theme.color("text").name()
        self.setStyleSheet(f"""
            QTableWidget {{
                background: {surface}; color: {text}; border: {FRAME_WIDTH}px solid {border};
                border-radius: {FRAME_RADIUS}px; outline: 0;
            }}
            QTableWidget::item {{ padding: 0 12px; }}
        """)

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
            self._set_hovered_row(self.rowAt(event.position().toPoint().y()))
        elif event.type() == QEvent.Type.Leave:
            self._set_hovered_row(-1)
        return super().viewportEvent(event)

    def updateGeometries(self):
        super().updateGeometries()
        # Mirror the table's vertical controller in a scrollbar over its right edge.
        scroll = self.verticalScrollBar()
        overlay = self._scrollbar_overlay
        with QSignalBlocker(overlay):
            overlay.setPageStep(scroll.pageStep())
            overlay.setSingleStep(scroll.singleStep())
            overlay.setRange(scroll.minimum(), scroll.maximum())
            overlay.setValue(scroll.value())
        bounds = self.viewport().geometry()
        width = overlay.sizeHint().width()
        overlay.setGeometry(bounds.right() + 1 - width, bounds.top(), width, bounds.height())
        overlay.setVisible(scroll.maximum() > scroll.minimum())
        overlay.raise_()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        viewport = self.viewport()
        # Qt can copy scrolled pixels from the rounded edge into the middle of the view.
        viewport.update()
        if viewport.underMouse():
            self._set_hovered_row(self.rowAt(viewport.mapFromGlobal(QCursor.pos()).y()))

    def _set_hovered_row(self, row):
        if self.hovered_row != row:
            self.hovered_row = row
            self.viewport().update()


class WorkshopScrollBar(QScrollBar):
    """Use Qt's scroll controls with a compact thumb and a track on hover."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        # Keep the hit areas and slider geometry identical in both visual states.
        self.setStyleSheet("""
            QScrollBar:vertical { background: transparent; width: 14px; margin: 14px 0; }
            QScrollBar::handle:vertical { min-height: 24px; margin: 0; }
            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
                subcontrol-origin: margin; width: 14px; height: 14px;
            }
            QScrollBar::sub-line:vertical { subcontrol-position: top; }
            QScrollBar::add-line:vertical { subcontrol-position: bottom; }
        """)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.sliderReleased.connect(self.update)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        control = QStyle.ComplexControl.CC_ScrollBar
        sub = QStyle.SubControl
        style = self.style()
        slider = QRectF(style.subControlRect(control, option, sub.SC_ScrollBarSlider, self))
        expanded = self.underMouse() or self.isSliderDown()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if expanded:
            painter.setBrush(theme.color("control" if theme.is_dark() else "control_hover"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 7, 7)
            for button, direction, limit in (
                (sub.SC_ScrollBarSubLine, -1, self.minimum()),
                (sub.SC_ScrollBarAddLine, 1, self.maximum()),
            ):
                center = QRectF(style.subControlRect(control, option, button, self)).center()
                x, y = center.x(), center.y()
                painter.setBrush(
                    theme.color("text_disabled" if self.value() == limit else "text_secondary")
                )
                painter.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(x - 4, y - direction * 2.5),
                            QPointF(x + 4, y - direction * 2.5),
                            QPointF(x, y + direction * 2.5),
                        ]
                    )
                )
        width = 6 if expanded else 2
        center_x = slider.center().x() if expanded else slider.right() - 4
        slider = QRectF(center_x - width / 2, slider.top(), width, slider.height())
        painter.setBrush(theme.color("text_secondary" if expanded else "text_tertiary"))
        painter.drawRoundedRect(slider, slider.width() / 2, slider.width() / 2)


class WorkshopItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        cell = option.rect
        viewport = self.parent().viewport().rect()
        painter.save()
        if cell.bottom() > viewport.bottom() - INNER_RADIUS:
            bounds = QRectF(viewport)
            clip = QPainterPath()
            clip.setFillRule(Qt.FillRule.WindingFill)
            clip.addRoundedRect(bounds, INNER_RADIUS, INNER_RADIUS)
            clip.addRect(bounds.adjusted(0, 0, 0, -INNER_RADIUS))
            antialiasing = QPainter.RenderHint.Antialiasing
            was_antialiased = painter.testRenderHint(antialiasing)
            painter.setRenderHint(antialiasing)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(antialiasing, was_antialiased)
        option.state &= ~QStyle.StateFlag.State_MouseOver
        if option.state & QStyle.StateFlag.State_Selected:
            surface = theme.color("surface")
            accent = theme.accent()
            selected = QColor.fromRgbF(
                surface.redF() * 0.86 + accent.redF() * 0.14,
                surface.greenF() * 0.86 + accent.greenF() * 0.14,
                surface.blueF() * 0.86 + accent.blueF() * 0.14,
            )
            option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
            option.backgroundBrush = QBrush(selected)
        elif index.row() == self.parent().hovered_row:
            hover = theme.color("control" if theme.is_dark() else "control_hover")
            option.backgroundBrush = QBrush(hover)
        option.widget.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget
        )
        # Cell dividers stop at the frame even when the final column is clipped.
        painter.setPen(theme.color("border"))
        if cell.right() < viewport.right():
            painter.drawLine(cell.topRight(), cell.bottomRight())
        if cell.bottom() < viewport.bottom():
            painter.drawLine(cell.bottomLeft(), cell.bottomRight())
        painter.restore()


class WorkshopHeader(QHeaderView):
    """Keep the workshop's joined header cells and rounded outer top corners."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        # Remove the inherited item-view frame without changing viewport geometry.
        self.setStyleSheet("QHeaderView { background: transparent; border: 0; border-radius: 0; }")
        self.viewport().setAutoFillBackground(False)

    def paintEvent(self, event):
        rect = self.viewport().rect()
        bounds = QRectF(rect)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.addRoundedRect(bounds, INNER_RADIUS, INNER_RADIUS)
        path.addRect(bounds.adjusted(0, INNER_RADIUS, 0, 0))
        # Paint the rounded surface in viewport coordinates, independently of section widths.
        with QPainter(self.viewport()) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillPath(path, theme.color("surface_alt"))
            painter.setPen(theme.color("border"))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        super().paintEvent(event)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        if rect.right() < self.viewport().rect().right():
            painter.setPen(theme.color("border"))
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

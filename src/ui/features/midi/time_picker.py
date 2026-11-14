"""Two-column duration picker for the MIDI playback timer."""

from ok.ui.qt.kit import Label, Popover, icon, theme
from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QTime,
    Signal,
)
from PySide6.QtGui import QKeySequence, QPainter, QRegion, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.foundation.widgets.separator import Separator

ROW_HEIGHT = 37
VISIBLE_ROWS = 9
CENTER_ROW = VISIBLE_ROWS // 2


class _WheelText(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = option.rect
        band = QRect(row.left(), CENTER_ROW * ROW_HEIGHT, row.width(), ROW_HEIGHT)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        painter.save()
        painter.setFont(option.font)
        painter.setClipRegion(QRegion(row) - QRegion(band), Qt.ClipOperation.IntersectClip)
        painter.setPen(theme.color("text"))
        painter.drawText(row, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
        if row.intersects(band):
            painter.save()
            painter.setFont(option.font)
            painter.setClipRect(row.intersected(band), Qt.ClipOperation.IntersectClip)
            painter.setPen(theme.color("on_accent"))
            painter.drawText(row, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()


class _TimeWheel(QListWidget):
    def __init__(self, name, count, digits, parent):
        super().__init__(parent)
        self._count = count
        self.setAccessibleName(name)
        self.setFixedHeight(ROW_HEIGHT * VISIBLE_ROWS)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(_WheelText(self))
        scroll_bar = self.verticalScrollBar()
        # Repaint text as it crosses the stationary selection band.
        scroll_bar.valueChanged.connect(self.viewport().update)
        self._scroll_animation = QPropertyAnimation(scroll_bar, b"value", self)
        self._scroll_animation.setDuration(250)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Repeat the values so the centered row can cross midnight without blank cells.
        for value in list(range(count)) * 3:
            item = QListWidgetItem(f"{value:0{digits}d}", self)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
        self.currentRowChanged.connect(self._center_row)

        self._up = self._step_button("chevron-up", -1)
        self._down = self._step_button("chevron-down", 1)

    def _step_button(self, glyph, step):
        button = QToolButton(self)
        button.setIcon(icon(glyph))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedHeight(32)
        button.setAutoRepeat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self._step(step))
        button.hide()
        return button

    def set_value(self, value):
        self._scroll_animation.stop()
        row = self._count + value
        with QSignalBlocker(self):
            self.setCurrentRow(row)
        self.verticalScrollBar().setValue((row - CENTER_ROW) * ROW_HEIGHT)

    def value(self):
        return self.currentRow() % self._count

    def _center_row(self, row):
        if row < 0:
            return
        self._scroll_animation.stop()
        scroll_bar = self.verticalScrollBar()
        start = scroll_bar.value()
        if row < CENTER_ROW or row >= self.count() - CENTER_ROW:
            centered = self._count + row % self._count
            start += (centered - row) * ROW_HEIGHT
            with QSignalBlocker(self):
                self.setCurrentRow(centered)
            scroll_bar.setValue(start)
            row = centered
        target = (row - CENTER_ROW) * ROW_HEIGHT
        if start != target:
            self._scroll_animation.setStartValue(start)
            self._scroll_animation.setEndValue(target)
            self._scroll_animation.start()

    def _step(self, direction):
        self.setCurrentRow(self.currentRow() + direction)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self._step(-1 if delta > 0 else 1)
        event.accept()

    def enterEvent(self, event):
        self._up.show()
        self._down.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._up.hide()
        self._down.hide()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._scroll_animation.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._up.setGeometry(0, 0, self.width(), self._up.height())
        self._down.setGeometry(
            0, self.height() - self._down.height(), self.width(), self._down.height()
        )

    def refresh_style(self):
        tokens = theme.tokens()
        self.setStyleSheet(
            "QListWidget { background: transparent; border: 0; outline: 0; }"
            f"QToolButton {{ background: {tokens['surface_alt']}; border: 0; }}"
            f"QToolButton:hover {{ background: {tokens['control_hover']}; }}"
        )
        self._up.setIcon(icon("chevron-up"))
        self._down.setIcon(icon("chevron-down"))
        self.viewport().update()


class _WheelSurface(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.width() // 2, 0, 1, self.height(), theme.color("separator"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.accent())
        painter.drawRoundedRect(
            QRectF(5, CENTER_ROW * ROW_HEIGHT, self.width() - 10, ROW_HEIGHT), 5, 5
        )


class ScheduleTimePicker(QPushButton):
    """Commit an elapsed playback duration after confirming both columns."""

    timeChanged = Signal(QTime)

    def __init__(self, accessible_name, parent=None):
        super().__init__(parent)
        self.setObjectName("scheduleTimePicker")
        self.setFixedSize(240, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(accessible_name)
        self._time = QTime(0, 0)
        self._hour_label = Label("0", parent=self)
        self._minute_label = Label("00", parent=self)
        face = QHBoxLayout(self)
        face.setContentsMargins(1, 1, 1, 1)
        face.setSpacing(0)
        divider = QFrame(self)
        divider.setObjectName("scheduleTimeDivider")
        divider.setFixedWidth(1)
        for widget in (self._hour_label, divider, self._minute_label):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for label in (self._hour_label, self._minute_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        face.addWidget(self._hour_label, 1)
        face.addWidget(divider)
        face.addWidget(self._minute_label, 1)

        self._popover = Popover(self)
        self._popover.setObjectName("scheduleTimePanel")
        self._popover.setFixedWidth(258)
        self.destroyed.connect(self._popover.deleteLater)
        panel = QVBoxLayout(self._popover)
        panel.setContentsMargins(1, 1, 1, 1)
        panel.setSpacing(0)
        self._surface = _WheelSurface(self._popover)
        columns = QHBoxLayout(self._surface)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        self._hours = _TimeWheel("HH", 24, 1, self._surface)
        self._minutes = _TimeWheel("mm", 60, 2, self._surface)
        columns.addWidget(self._hours, 1)
        columns.addWidget(self._minutes, 1)
        panel.addWidget(self._surface)
        panel.addWidget(Separator(self._popover))

        actions = QHBoxLayout()
        actions.setContentsMargins(3, 3, 3, 3)
        actions.setSpacing(4)
        self._confirm_button = self._action_button("OK", self._confirm)
        self._cancel_button = self._action_button("Cancel", self._dismiss)
        actions.addWidget(self._confirm_button, 1)
        actions.addWidget(self._cancel_button, 1)
        panel.addLayout(actions)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._popover)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self._dismiss)

        self.clicked.connect(self._toggle_picker)
        theme.changed.connect(self._refresh_style)
        self._refresh_style()

    def _action_button(self, text, action):
        button = QToolButton(self._popover)
        button.setObjectName("scheduleTimeAction")
        name = QCoreApplication.translate("QPlatformTheme", text)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFixedHeight(33)
        button.setIconSize(QSize(16, 16))
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(action)
        return button

    def _refresh_style(self):
        tokens = theme.tokens()
        self.setStyleSheet(
            "QPushButton#scheduleTimePicker { border-radius: 5px;"
            f" background: {tokens['control']}; border: 1px solid {tokens['border_strong']}; }}"
            f"QPushButton#scheduleTimePicker:hover {{ background: {tokens['control_hover']}; }}"
            f"QPushButton#scheduleTimePicker:focus {{ border-color: {theme.accent().name()}; }}"
            "QPushButton#scheduleTimePicker:disabled { background: transparent;"
            f" border-color: {tokens['border']}; }}"
            f"QFrame#scheduleTimeDivider {{ background: {tokens['border_strong']}; }}"
            f"QFrame#scheduleTimeDivider:disabled {{ background: {tokens['separator']}; }}"
        )
        self._popover.setStyleSheet(
            "Popover#scheduleTimePanel { border-radius: 6px;"
            f" background: {tokens['surface_alt']}; border: 1px solid {tokens['border_strong']}; }}"
            "QToolButton#scheduleTimeAction { background: transparent;"
            " border: 1px solid transparent; border-radius: 5px; }"
            f"QToolButton#scheduleTimeAction:hover {{ background: {tokens['control_hover']}; }}"
            f"QToolButton#scheduleTimeAction:focus {{ border-color: {theme.accent().name()}; }}"
        )
        self._confirm_button.setIcon(icon("check"))
        self._cancel_button.setIcon(icon("x"))
        self._hours.refresh_style()
        self._minutes.refresh_style()
        self._surface.update()

    def time(self):
        return self._time

    def setTime(self, value):
        if not value.isValid() or value == self._time:
            return
        self._time = value
        self._hour_label.setText(str(value.hour()))
        self._minute_label.setText(value.toString("mm"))
        self.timeChanged.emit(value)

    def _toggle_picker(self):
        if self._popover.isVisible():
            self._dismiss()
            return
        self._popover.popup(self)
        self._hours.set_value(self._time.hour())
        self._minutes.set_value(self._time.minute())
        self._hours.setFocus(Qt.FocusReason.PopupFocusReason)
        # Align the center row with the original field, clamped to the window edges.
        window = self.window()
        center = self.mapTo(window, self.rect().center())
        x = min(center.x() - self._popover.width() // 2, window.width() - self._popover.width() - 8)
        y = min(
            center.y() - self._surface.height() // 2, window.height() - self._popover.height() - 8
        )
        self._popover.move(QPoint(max(8, x), max(8, y)))

    def _confirm(self):
        self.setTime(QTime(self._hours.value(), self._minutes.value()))
        self._dismiss()

    def _dismiss(self):
        self._popover.close_popover()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def hideEvent(self, event):
        self._popover.close_popover()
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._popover.close_popover()
        super().changeEvent(event)

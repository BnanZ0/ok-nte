"""Widgets used only by the MIDI player tab."""

from ok import og
from ok.ui.qt.kit import Label, icon, theme
from PySide6.QtCore import QLocale, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QProxyStyle,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionButton,
    QStyleOptionSlider,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from src.midi_player.models import MidiTrackInfo
from src.ui.foundation.widgets.lists import CollectionItemDelegate


class _PlaybackSliderStyle(QProxyStyle):
    """Scale native slider painting and hit areas together without replacing its skin."""

    SCALE = 1.25

    def _unscaled_option(self, option):
        native = QStyleOptionSlider(option)
        rect = option.rect
        native.rect = QRectF(
            rect.x() / self.SCALE,
            rect.y() / self.SCALE,
            rect.width() / self.SCALE,
            rect.height() / self.SCALE,
        ).toRect()
        return native

    def pixelMetric(self, metric, option=None, widget=None):
        value = super().pixelMetric(metric, option, widget)
        if widget is not None and metric in (
            QStyle.PixelMetric.PM_SliderLength,
            QStyle.PixelMetric.PM_SliderThickness,
            QStyle.PixelMetric.PM_SliderControlThickness,
        ):
            return round(value * self.SCALE)
        return value

    def subControlRect(self, control, option, sub_control, widget=None):
        if control == QStyle.ComplexControl.CC_Slider and widget is not None:
            # A null widget keeps native geometry queries from being scaled twice.
            rect = super().subControlRect(control, self._unscaled_option(option), sub_control, None)
            return QRectF(
                rect.x() * self.SCALE,
                rect.y() * self.SCALE,
                rect.width() * self.SCALE,
                rect.height() * self.SCALE,
            ).toRect()
        return super().subControlRect(control, option, sub_control, widget)

    def drawComplexControl(self, control, option, painter, widget=None):
        if control == QStyle.ComplexControl.CC_Slider and widget is not None:
            painter.save()
            painter.scale(self.SCALE, self.SCALE)
            super().drawComplexControl(control, self._unscaled_option(option), painter, None)
            painter.restore()
        else:
            super().drawComplexControl(control, option, painter, widget)


class PlaybackSlider(QSlider):
    """Horizontal playback slider with larger native controls."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._size_style = _PlaybackSliderStyle()
        self._size_style.setParent(self)
        self.setStyle(self._size_style)
        self.setMinimumHeight(32)


class TrackListScroll(QScrollArea):
    """Prefer the track list's natural height while allowing it to shrink and scroll."""

    def sizeHint(self):
        content = self.widget()
        return content.sizeHint() if content is not None else super().sizeHint()

    def minimumSizeHint(self):
        return QSize(0, 28)


class TrackCheckBox(QCheckBox):
    """A compact track row with native checkbox input and aligned metadata."""

    def __init__(self, track: MidiTrackInfo, index_digits: int, row_index: int, parent=None):
        self.track = track
        self._number = str(track.index + 1)
        self._note_count = QLocale().toString(track.note_count)
        self._index_digits = index_digits
        self._row_index = row_index
        full_text = f"{self._number}. {track.name} ({track.note_count})"
        super().__init__(full_text, parent)
        self.setAccessibleName(full_text)
        self.setToolTip(full_text)
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        option.rect = option.rect.adjusted(8, 0, 0, 0)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = self.rect().adjusted(2, 1, -2, -1)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("control_hover"))
            painter.drawRoundedRect(row, 5, 5)
        elif self._row_index % 2:
            painter.fillRect(row, theme.color("surface_alt"))

        self.style().drawControl(QStyle.ControlElement.CE_CheckBox, option, painter, self)

        metadata_font = QFont(self.font())
        metadata_font.setPixelSize(11)
        metadata_metrics = QFontMetrics(metadata_font)
        name_metrics = QFontMetrics(self.font())
        number_width = max(
            metadata_metrics.horizontalAdvance("0" * self._index_digits),
            metadata_metrics.horizontalAdvance(self._number),
        ) + metadata_metrics.horizontalAdvance(".")
        count_width = max(36, metadata_metrics.horizontalAdvance(self._note_count))
        number_rect = QRect(indicator.right() + 10, 0, number_width, self.height())
        count_rect = QRect(self.width() - count_width - 10, 0, count_width, self.height())
        name_rect = QRect(
            number_rect.right() + 10,
            0,
            max(0, count_rect.left() - number_rect.right() - 18),
            self.height(),
        )

        metadata_color = theme.color("text_secondary" if enabled else "text_disabled")
        painter.setFont(metadata_font)
        painter.setPen(metadata_color)
        right_center = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(number_rect, right_center, f"{self._number}.")
        painter.drawText(count_rect, right_center, self._note_count)
        painter.setFont(self.font())
        painter.setPen(theme.color("text" if enabled else "text_disabled"))
        name = name_metrics.elidedText(
            self.track.name, Qt.TextElideMode.ElideRight, name_rect.width()
        )
        left_center = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(name_rect, left_center, name)

        if (
            enabled
            and self.hasFocus()
            and self.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
        ):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.accent(), 1))
            painter.drawRoundedRect(row.adjusted(0, 0, -1, -1), 5, 5)


class SongLibraryTree(QTreeWidget):
    """Keep Qt's tree interaction with themed playlist rows and disclosure glyphs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(CollectionItemDelegate(self, tree_groups=True))

    def drawBranches(self, painter, rect, index):
        painter.fillRect(rect, theme.color("surface"))
        if not self.model().hasChildren(index):
            return
        size = 12
        x = (
            rect.left() + (self.indentation() - size) // 2
            if self.isRightToLeft()
            else rect.right() + 1 - (self.indentation() + size) // 2
        )
        if self.isExpanded(index):
            arrow = "chevron-down"
        else:
            arrow = "chevron-left" if self.isRightToLeft() else "chevron-right"
        glyph = QRect(x, rect.center().y() - size // 2, size, size)
        icon(arrow, "text_secondary").paint(painter, glyph)


class _MarqueeLabelMixin:
    def _init_marquee(self):
        self._marquee_text = super().text()
        self._marquee_offset = 0
        self._marquee_gap = 36
        self._marquee_timer = QTimer(self)
        self._marquee_timer.setInterval(35)
        self._marquee_timer.timeout.connect(self._advance_marquee)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setText(self, text):
        if not hasattr(self, "_marquee_timer"):
            super().setText(text)
            return
        self._marquee_text = text
        self._marquee_offset = 0
        self.setToolTip(text)
        super().setText(text)
        self._sync_marquee_timer()
        self.update()

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(hint.width(), 240), hint.height())

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(80, hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_marquee_timer()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_marquee_timer()

    def hideEvent(self, event):
        self._marquee_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if not self._needs_marquee():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(self._marquee_text)
        y = int((self.height() + metrics.ascent() - metrics.descent()) / 2)
        x = -self._marquee_offset
        painter.drawText(x, y, self._marquee_text)
        painter.drawText(x + text_width + self._marquee_gap, y, self._marquee_text)

    def _needs_marquee(self):
        text = getattr(self, "_marquee_text", super().text())
        if not text:
            return False
        return QFontMetrics(self.font()).horizontalAdvance(text) > max(0, self.width())

    def _advance_marquee(self):
        if not self._needs_marquee():
            self._marquee_offset = 0
            self._marquee_timer.stop()
            self.update()
            return
        text_width = QFontMetrics(self.font()).horizontalAdvance(self._marquee_text)
        self._marquee_offset = (self._marquee_offset + 1) % (text_width + self._marquee_gap)
        self.update()

    def _sync_marquee_timer(self):
        if self.isVisible() and self._needs_marquee():
            if not self._marquee_timer.isActive():
                self._marquee_timer.start()
        else:
            self._marquee_timer.stop()
            self._marquee_offset = 0


class MarqueeBodyLabel(_MarqueeLabelMixin, Label):
    def __init__(self, text="", parent=None):
        super().__init__("", "body", parent)
        self._init_marquee()
        self.setText(text)


class MarqueeTitleLabel(_MarqueeLabelMixin, Label):
    def __init__(self, text="", parent=None):
        super().__init__("", "title", parent)
        self._init_marquee()
        self.setText(text)


class PitchChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(180)
        self.setMouseTracking(True)
        self.note_counts = {}
        self.min_playable = 48
        self.max_playable = 83
        self.playable_pitches = set(range(48, 84))
        self.hovered_note = None

    def set_data(self, note_counts, min_playable, max_playable, playable_pitches=None):
        self.note_counts = note_counts
        self.min_playable = min_playable
        self.max_playable = max_playable
        if playable_pitches is None:
            self.playable_pitches = set(range(min_playable, max_playable + 1))
        else:
            self.playable_pitches = set(playable_pitches)
        self.hovered_note = None
        self.update()

    def _is_note_playable(self, note):
        return note in self.playable_pitches

    def mouseMoveEvent(self, event):
        if not self.note_counts:
            return

        w = self.width()
        min_note = min(35, min(self.note_counts.keys()))
        max_note = max(90, max(self.note_counts.keys()))
        note_range = max_note - min_note + 1
        spacing = (w - 20) / note_range

        x = event.position().x()
        if 10 <= x <= w - 10:
            note_idx = round((x - 10) / spacing)
            hovered = min_note + note_idx
            if hovered != self.hovered_note:
                self.hovered_note = hovered
                self.update()
        else:
            if self.hovered_note is not None:
                self.hovered_note = None
                self.update()

    def leaveEvent(self, event):
        self.hovered_note = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height() - 30  # Leave space for rotated text

        # Background line
        painter.setPen(QPen(theme.color("border_strong"), 1))
        painter.drawLine(0, h, w, h)

        if not self.note_counts:
            return

        min_note = min(35, min(self.note_counts.keys()))
        max_note = max(90, max(self.note_counts.keys()))

        note_range = max_note - min_note + 1

        max_count = max(self.note_counts.values()) if self.note_counts else 1
        if max_count == 0:
            max_count = 1

        spacing = (w - 20) / note_range
        bar_width = max(3.0, spacing * 0.5)
        playable_color = theme.accent()
        unplayable_color = theme.color("danger")
        axis_pen = QPen(theme.color("text_secondary"))

        for i, note in enumerate(range(min_note, max_note + 1)):
            count = self.note_counts.get(note, 0)
            is_playable = self._is_note_playable(note)
            color = playable_color if is_playable else unplayable_color

            x = 10 + i * spacing
            if count > 0:
                min_bar_h = max(8.0, bar_width)
                bar_h = max(min_bar_h, (count / max_count) * (h - 20))
                y_bottom = h - bar_width / 2
                y_top = h - bar_h + bar_width / 2

                pen = QPen(color, bar_width, Qt.SolidLine, Qt.RoundCap)
                painter.setPen(pen)
                painter.drawLine(int(x), int(y_bottom), int(x), int(y_top))

            # Draw x-axis labels
            if note % 5 == 0:
                painter.save()
                painter.translate(x, h + 15)
                painter.rotate(45)
                painter.setPen(axis_pen)
                painter.setFont(QFont("Arial", 8))
                painter.drawText(0, 0, str(note))
                painter.restore()

        # Draw tooltip
        if self.hovered_note is not None and min_note <= self.hovered_note <= max_note:
            self._draw_tooltip(painter, min_note, spacing, h)

    def _draw_tooltip(self, painter, min_note, spacing, base_h):
        note = self.hovered_note
        count = self.note_counts.get(note, 0)
        is_playable = self._is_note_playable(note)

        playable_c = count if is_playable else 0
        unplayable_c = count if not is_playable else 0

        x = 10 + (note - min_note) * spacing
        y = base_h - 40  # Float above the line

        box_w = 120
        box_h = 80
        box_x = x - box_w / 2
        box_y = y - box_h

        # Keep tooltip in bounds
        if box_x < 5:
            box_x = 5
        if box_x + box_w > self.width() - 5:
            box_x = self.width() - box_w - 5
        if box_y < 5:
            box_y = 5

        # Shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawRoundedRect(int(box_x + 2), int(box_y + 2), int(box_w), int(box_h), 8, 8)

        # Tooltip body
        painter.setBrush(theme.color("surface_alt"))
        painter.setPen(QPen(theme.color("border_strong")))
        painter.drawRoundedRect(int(box_x), int(box_y), int(box_w), int(box_h), 8, 8)

        # Note number
        painter.setPen(QPen(theme.color("text")))
        painter.setFont(QFont("Arial", 11, QFont.Weight.Normal))
        painter.drawText(
            int(box_x), int(box_y + 5), int(box_w), 25, Qt.AlignHCenter | Qt.AlignVCenter, str(note)
        )

        # Unplayable notes
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("danger"))
        painter.drawEllipse(int(box_x + 10), int(box_y + 40), 10, 10)

        # Playable notes
        painter.setBrush(theme.accent())
        painter.drawEllipse(int(box_x + 10), int(box_y + 60), 10, 10)

        # Texts
        painter.setPen(QPen(theme.color("text_secondary")))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            int(box_x + 28),
            int(box_y + 38),
            60,
            15,
            Qt.AlignLeft | Qt.AlignVCenter,
            og.app.tr("无法弹奏"),
        )
        painter.drawText(
            int(box_x + 28),
            int(box_y + 58),
            60,
            15,
            Qt.AlignLeft | Qt.AlignVCenter,
            og.app.tr("可以弹奏"),
        )

        # Counts
        painter.drawText(
            int(box_x + 85),
            int(box_y + 38),
            25,
            15,
            Qt.AlignRight | Qt.AlignVCenter,
            str(unplayable_c),
        )
        painter.drawText(
            int(box_x + 85),
            int(box_y + 58),
            25,
            15,
            Qt.AlignRight | Qt.AlignVCenter,
            str(playable_c),
        )


class KeyConfigWidget(QFrame):
    def __init__(self, default_x1, default_y1, default_x2, default_y2, parent=None):
        super().__init__(parent)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 0, 0, 0)

        # Top-Left Key Center
        self.hbox1 = QHBoxLayout()
        self.lbl_top_left = Label(og.app.tr("左上按键中心: "), "body")
        self.lbl_x1 = Label("X1:", "body")
        self.spn_x1 = QLineEdit()
        self.spn_x1.setText(str(default_x1))
        self.spn_x1.setFixedWidth(120)

        self.lbl_y1 = Label("Y1:", "body")
        self.spn_y1 = QLineEdit()
        self.spn_y1.setText(str(default_y1))
        self.spn_y1.setFixedWidth(120)

        self.hbox1.addWidget(self.lbl_top_left)
        self.hbox1.addStretch()
        self.hbox1.addWidget(self.lbl_x1)
        self.hbox1.addWidget(self.spn_x1)
        self.hbox1.addSpacing(10)
        self.hbox1.addWidget(self.lbl_y1)
        self.hbox1.addWidget(self.spn_y1)

        # Bottom-Right Key Center
        self.hbox2 = QHBoxLayout()
        self.lbl_bottom_right = Label(og.app.tr("右下按键中心: "), "body")
        self.lbl_x2 = Label("X2:", "body")
        self.spn_x2 = QLineEdit()
        self.spn_x2.setText(str(default_x2))
        self.spn_x2.setFixedWidth(120)

        self.lbl_y2 = Label("Y2:", "body")
        self.spn_y2 = QLineEdit()
        self.spn_y2.setText(str(default_y2))
        self.spn_y2.setFixedWidth(120)

        self.hbox2.addWidget(self.lbl_bottom_right)
        self.hbox2.addStretch()
        self.hbox2.addWidget(self.lbl_x2)
        self.hbox2.addWidget(self.spn_x2)
        self.hbox2.addSpacing(10)
        self.hbox2.addWidget(self.lbl_y2)
        self.hbox2.addWidget(self.spn_y2)

        self.vbox.addLayout(self.hbox1)
        self.vbox.addLayout(self.hbox2)

    def get_coords(self):
        try:
            return (
                float(self.spn_x1.text()),
                float(self.spn_y1.text()),
                float(self.spn_x2.text()),
                float(self.spn_y2.text()),
            )
        except ValueError:
            return (0.0, 0.0, 0.0, 0.0)

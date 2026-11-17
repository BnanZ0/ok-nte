import unittest

from ok.ui.qt.kit import Theme, theme
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider, QWidget

from src.ui.features.midi.widgets import PlaybackSlider
from tests.support.ok_runtime import isolate_qt_widgets
from tests.support.qt_events import send_key


class TestPlaybackSlider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        isolate_qt_widgets(self)
        previous_theme = theme.mode
        self.addCleanup(theme.set_theme, previous_theme)
        self.root = QWidget()
        theme.install(self.root)
        self.slider = PlaybackSlider(self.root)
        self.slider.resize(320, 32)
        self.slider.setRange(25, 200)

    def mouse_event(self, event_type, point):
        position = QPointF(point)
        button = (
            Qt.MouseButton.NoButton
            if event_type == QEvent.Type.MouseMove
            else Qt.MouseButton.LeftButton
        )
        buttons = (
            Qt.MouseButton.NoButton
            if event_type == QEvent.Type.MouseButtonRelease
            else Qt.MouseButton.LeftButton
        )
        event = QMouseEvent(
            event_type,
            position,
            QPointF(self.slider.mapToGlobal(point)),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QCoreApplication.sendEvent(self.slider, event)

    def test_larger_handle_can_be_grabbed_and_dragged_to_both_endpoints(self):
        for mode in (Theme.LIGHT, Theme.DARK):
            for direction in (Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft):
                with self.subTest(theme=mode, direction=direction):
                    theme.set_theme(mode)
                    self.slider.setLayoutDirection(direction)
                    self.slider.setValue(100)
                    option = QStyleOptionSlider()
                    self.slider.initStyleOption(option)
                    handle = self.slider.style().subControlRect(
                        QStyle.ComplexControl.CC_Slider,
                        option,
                        QStyle.SubControl.SC_SliderHandle,
                        self.slider,
                    )
                    grab = QPoint(handle.left() + 1, handle.center().y())
                    self.mouse_event(QEvent.Type.MouseButtonPress, grab)
                    self.assertTrue(self.slider.isSliderDown())
                    for x, expected in ((0, 25), (self.slider.width() - 1, 200)):
                        if direction == Qt.LayoutDirection.RightToLeft:
                            expected = 225 - expected
                        self.mouse_event(QEvent.Type.MouseMove, QPoint(x, grab.y()))
                        self.assertEqual(self.slider.value(), expected)
                    self.mouse_event(QEvent.Type.MouseButtonRelease, grab)
                    self.assertFalse(self.slider.isSliderDown())

    def test_keyboard_and_disabled_input_keep_native_behavior(self):
        send_key(self.slider, Qt.Key.Key_End)
        self.assertEqual(self.slider.value(), 200)
        send_key(self.slider, Qt.Key.Key_Home)
        self.assertEqual(self.slider.value(), 25)
        self.slider.setEnabled(False)
        send_key(self.slider, Qt.Key.Key_End)
        self.assertEqual(self.slider.value(), 25)


if __name__ == "__main__":
    unittest.main()

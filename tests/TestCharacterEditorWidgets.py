import unittest

from ok.ui.qt.kit import Theme, theme
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QScrollBar,
    QWidget,
)

from src.ui.features.characters.editor_widgets import CharacterTitleLabel, ComboEditorFrame
from src.ui.foundation.widgets.table import ThemedTable
from tests.support.ok_runtime import isolate_qt_widgets
from tests.support.qt_events import click_at


class TestCharacterEditorWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        isolate_qt_widgets(self)
        previous_theme = theme.mode
        self.addCleanup(theme.set_theme, previous_theme)
        self.root = QWidget()
        self.root.resize(500, 300)
        theme.install(self.root)

    @staticmethod
    def pixel(widget, x, y):
        image = widget.grab().toImage()
        dpr = image.devicePixelRatio()
        return image.pixelColor(round(x * dpr), round(y * dpr))

    def test_title_keeps_typography_and_literal_names_after_theme_changes(self):
        name = "<b>角色 & 名称</b>  A\nB"
        title = CharacterTitleLabel(name, self.root)
        title.setWordWrap(True)
        for mode in (Theme.LIGHT, Theme.DARK):
            with self.subTest(theme=mode):
                theme.set_theme(mode)
                title.grab()
                document = QTextDocument()
                document.setHtml(QLabel.text(title))
                self.assertEqual(title.text(), name)
                self.assertEqual(document.toPlainText(), name)
                font = document.begin().begin().fragment().charFormat().font()
                self.assertEqual(font.pixelSize(), 28)
                self.assertEqual(font.weight(), 600)

    def test_combo_frame_keeps_surface_and_focus_border_after_theme_changes(self):
        frame = ComboEditorFrame(self.root)
        frame.setGeometry(10, 10, 400, 140)
        other = QLineEdit(self.root)
        other.move(10, 200)
        self.root.show()
        self.root.activateWindow()
        self.qt_app.processEvents()
        for mode in (Theme.LIGHT, Theme.DARK):
            with self.subTest(theme=mode):
                theme.set_theme(mode)
                other.setFocus()
                self.qt_app.processEvents()
                self.assertEqual(self.pixel(frame, 200, 0), theme.color("border_strong"))
                self.assertEqual(self.pixel(frame, 390, 70), theme.color("surface_alt"))
                frame.editor.setFocus()
                self.qt_app.processEvents()
                self.assertTrue(frame.editor.hasFocus())
                self.assertEqual(self.pixel(frame, 200, 0), QColor(theme.accent().name()))

    def test_workshop_header_draws_only_inner_dividers_in_both_themes(self):
        table = ThemedTable(self.root)
        table.resize(450, 200)
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Name", "Version"])
        table.verticalHeader().hide()
        self.root.show()
        header = table.horizontalHeader()
        viewport = header.viewport()
        for mode in (Theme.LIGHT, Theme.DARK):
            with self.subTest(theme=mode):
                theme.set_theme(mode)
                self.qt_app.processEvents()
                image = viewport.grab().toImage()
                dpr = image.devicePixelRatio()
                y = round((viewport.height() - 4) * dpr)
                inner_edge = round(header.sectionViewportPosition(1) * dpr)
                last_edge = round(header.length() * dpr)
                self.assertIn(
                    theme.color("border"),
                    [image.pixelColor(x, y) for x in range(inner_edge - 2, inner_edge + 1)],
                )
                for x in range(last_edge - 2, last_edge + 1):
                    self.assertEqual(image.pixelColor(x, y), theme.color("surface_alt"))

    def test_workshop_hover_highlights_both_cells_without_selecting_the_row(self):
        table = ThemedTable(self.root)
        table.resize(450, 200)
        table.setRowCount(2)
        table.setColumnCount(2)
        table.selectRow(0)
        self.root.show()
        self.qt_app.processEvents()
        viewport = table.viewport()
        left = table.visualRect(table.model().index(1, 0))
        right = table.visualRect(table.model().index(1, 1))
        position = left.center()
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(position),
            QPointF(viewport.mapToGlobal(position)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        for mode in (Theme.LIGHT, Theme.DARK):
            with self.subTest(theme=mode):
                theme.set_theme(mode)
                QCoreApplication.sendEvent(viewport, move)
                hover = theme.color("control" if mode == Theme.DARK else "control_hover")
                for cell in (left, right):
                    self.assertEqual(self.pixel(viewport, cell.center().x(), position.y()), hover)
                self.assertEqual({index.row() for index in table.selectedIndexes()}, {0})
                QCoreApplication.sendEvent(viewport, QEvent(QEvent.Type.Leave))
                self.assertEqual(
                    self.pixel(viewport, right.center().x(), position.y()), theme.color("surface")
                )

    def test_workshop_right_frame_stays_in_place_during_column_drag(self):
        table = ThemedTable(self.root)
        table.resize(450, 200)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Name", "Category", "V"])
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalHeader().hide()
        table.setRowCount(20)
        header = table.horizontalHeader()
        for column, width in enumerate((130, 130, 90)):
            table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.root.show()
        self.qt_app.processEvents()
        viewport = header.viewport()
        self.assertEqual(header.length(), viewport.width())

        def right_edge():
            image = table.grab().toImage()
            width = round(8 * image.devicePixelRatio())
            return image.copy(image.width() - width, 0, width, image.height())

        before = right_edge()
        start = QPoint(header.sectionViewportPosition(2) - 1, viewport.height() // 2)
        end = start + QPoint(130, 0)
        left = Qt.MouseButton.LeftButton
        none = Qt.MouseButton.NoButton
        for kind, position, button, buttons in (
            (QEvent.Type.MouseButtonPress, start, left, left),
            (QEvent.Type.MouseMove, end, none, left),
            (QEvent.Type.MouseButtonRelease, end, left, none),
        ):
            event = QMouseEvent(
                kind,
                QPointF(position),
                QPointF(viewport.mapToGlobal(position)),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )
            QCoreApplication.sendEvent(viewport, event)
            self.qt_app.processEvents()
            self.assertEqual(right_edge(), before)
        self.assertGreater(header.length(), viewport.width())

    def test_workshop_bottom_corners_clip_hover_and_selection(self):
        table = ThemedTable(self.root)
        table.resize(450, 200)
        table.setColumnCount(2)
        table.setRowCount(20)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().hide()
        self.root.show()
        self.qt_app.processEvents()
        viewport = table.viewport()
        bounds = viewport.geometry()
        samples = (bounds.bottomLeft(), bounds.bottomRight(), bounds.bottomRight() - QPoint(40, 3))

        def colors():
            image = table.grab().toImage()
            dpr = image.devicePixelRatio()
            return tuple(
                image.pixelColor(round(point.x() * dpr), round(point.y() * dpr))
                for point in samples
            )

        scrollbar = table.verticalScrollBar()
        last_row = table.rowCount() - 1
        position = QPoint(viewport.width() // 2, viewport.height() - 4)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(position),
            QPointF(viewport.mapToGlobal(position)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        for clipped_pixels in (0, 12):
            with self.subTest(clipped_pixels=clipped_pixels):
                scrollbar.setValue(scrollbar.maximum() - clipped_pixels)
                table.clearSelection()
                QCoreApplication.sendEvent(viewport, QEvent(QEvent.Type.Leave))
                self.assertEqual(table.rowAt(position.y()), last_row)
                idle = colors()

                QCoreApplication.sendEvent(viewport, move)
                hovered = colors()
                QCoreApplication.sendEvent(viewport, QEvent(QEvent.Type.Leave))
                table.selectRow(last_row)
                selected = colors()

                for highlighted in (hovered, selected):
                    self.assertEqual(highlighted[:2], idle[:2])
                    self.assertNotEqual(highlighted[2], idle[2])

    def test_workshop_scrollbar_reveals_its_track_and_keeps_native_arrow_actions(self):
        table = ThemedTable(self.root)
        table.resize(450, 200)
        table.setColumnCount(1)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().hide()
        table.setRowCount(30)
        self.root.show()
        self.qt_app.processEvents()
        (bar,) = (bar for bar in table.findChildren(QScrollBar) if bar.isVisible())
        viewport = table.viewport().geometry()
        self.assertEqual(bar.y(), viewport.y())
        self.assertEqual(bar.geometry().right(), viewport.right())
        x = bar.width() // 2
        grid_x = bar.x() + 3
        grid_y = viewport.y() + table.rowHeight(0) - 1
        bar.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        self.assertEqual(self.pixel(table, grid_x, grid_y), theme.color("border"))
        compact_x = bar.geometry().right() + 1 - 4
        self.assertEqual(self.pixel(table, compact_x, bar.y() + 20), theme.color("text_tertiary"))
        self.assertNotEqual(
            self.pixel(table, bar.x() + x, bar.y() + 20), theme.color("text_tertiary")
        )
        bar.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
        self.assertNotEqual(self.pixel(table, grid_x, grid_y), theme.color("border"))
        self.assertEqual(
            self.pixel(table, bar.x() + x, bar.y() + 20), theme.color("text_secondary")
        )
        click_at(bar, QPoint(x, bar.height() - 7))
        self.assertGreater(bar.value(), 0)
        self.assertEqual(table.verticalScrollBar().value(), bar.value())
        click_at(bar, QPoint(x, 7))
        self.assertEqual(bar.value(), 0)
        table.scrollToBottom()
        self.assertEqual(bar.value(), bar.maximum())
        table.setRowCount(1)
        self.qt_app.processEvents()
        self.assertFalse(bar.isVisible())
        self.assertEqual(table.viewport().geometry(), viewport)


if __name__ == "__main__":
    unittest.main()

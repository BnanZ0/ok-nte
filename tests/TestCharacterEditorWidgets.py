import unittest

from ok.ui.qt.kit import Theme, theme
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from src.ui.features.characters.editor_widgets import CharacterTitleLabel, ComboEditorFrame
from src.ui.features.characters.workshop_table import WorkshopHeader, WorkshopItemDelegate
from tests.support.ok_runtime import isolate_qt_widgets


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

    def test_workshop_selection_and_header_keep_their_original_colors(self):
        table = QTableWidget(2, 2, self.root)
        table.resize(450, 200)
        table.setHorizontalHeader(WorkshopHeader(table))
        table.setItemDelegate(WorkshopItemDelegate(table))
        table.setHorizontalHeaderLabels(["Name", "Version"])
        table.verticalHeader().hide()
        for row in range(2):
            table.setItem(row, 0, QTableWidgetItem("Example"))
        table.selectRow(1)
        for mode in (Theme.LIGHT, Theme.DARK):
            with self.subTest(theme=mode):
                theme.set_theme(mode)
                self.root.grab()
                surface = theme.color("surface")
                accent = theme.accent()
                selected = QColor.fromRgbF(
                    surface.redF() * 0.86 + accent.redF() * 0.14,
                    surface.greenF() * 0.86 + accent.greenF() * 0.14,
                    surface.blueF() * 0.86 + accent.blueF() * 0.14,
                )
                selected = QColor(selected.name())
                cell = table.visualRect(table.model().index(1, 0))
                self.assertEqual(
                    self.pixel(table.viewport(), cell.right() - 5, cell.center().y()), selected
                )
                self.assertEqual(
                    self.pixel(table.horizontalHeader().viewport(), 50, 2),
                    theme.color("surface_alt"),
                )


if __name__ == "__main__":
    unittest.main()

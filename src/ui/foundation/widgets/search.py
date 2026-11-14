"""Reusable filtering behavior built on the kit and native Qt inputs."""

from ok.ui.qt.kit import ComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QLineEdit, QListWidget, QVBoxLayout, QWidget

from src.ui.foundation.widgets.lists import style_flat_collection


class SearchableComboBox(ComboBox):
    """Editable combo with a contains completer over its actual item model."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setEditable(True)
        self.setInsertPolicy(self.InsertPolicy.NoInsert)
        completer = QCompleter(self.model(), self)
        completer.setCompletionColumn(self.modelColumn())
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompleter(completer)


class SearchableListWidget(QWidget):
    """Search input and themed collection; callers operate on ``list_widget``."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search_edit = QLineEdit(self)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget(self)
        style_flat_collection(self.list_widget)
        layout.addWidget(self.list_widget)

    def setPlaceholderText(self, text: str):
        self.search_edit.setPlaceholderText(text)

    def _apply_filter(self, keyword: str):
        normalized = keyword.strip().casefold()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(normalized not in item.text().casefold())

    def reapply_filter(self):
        self._apply_filter(self.search_edit.text())

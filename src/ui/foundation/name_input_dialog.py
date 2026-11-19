from collections.abc import Callable

from ok.ui.qt.kit import Label
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from src.ui.foundation.dialogs import FormDialog
from src.ui.foundation.i18n import translate


class NameInputDialog(FormDialog):
    """Name editor with inline business validation."""

    def __init__(
        self,
        name: str = "",
        *,
        title: str = "重命名",
        placeholder: str = "名称",
        title_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        name_validator: Callable[[str], bool] | None = None,
        error_message: str = "",
        parent=None,
    ):
        super().__init__(parent, title=title, width=360)
        self.name_validator = name_validator
        self.title_label.setText(translate(title))
        self.title_label.setAlignment(title_alignment)

        self.name_edit = QLineEdit(self.panel)
        self.name_edit.setPlaceholderText(translate(placeholder))
        self.name_edit.setText(name)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.textChanged.connect(self._validate_name)
        self.body.addWidget(self.name_edit)

        self.name_error_label = Label(
            translate(error_message), "caption", self.panel, word_wrap=True
        )
        self.name_error_label.setProperty("tone", "danger")
        self.name_error_label.setVisible(False)
        self.body.addWidget(self.name_error_label)

        self.ok_button.setText(translate("OK"))
        self.cancel_button.setText(translate("取消"))
        self._validate_name(name)

    def open(self) -> None:
        super().open()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _validate_name(self, name: str) -> None:
        normalized_name = name.strip()
        is_valid = bool(normalized_name) and (
            self.name_validator is None or self.name_validator(normalized_name)
        )
        self.ok_button.setEnabled(is_valid)
        self.name_error_label.setVisible(
            bool(normalized_name) and not is_valid and bool(self.name_error_label.text())
        )

    def validate(self) -> bool:
        self._validate_name(self.name_edit.text())
        return self.ok_button.isEnabled()

    def get_name(self) -> str:
        return self.name_edit.text().strip()

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from qfluentwidgets import CaptionLabel, LineEdit, SubtitleLabel

from src.ui.foundation.dialogs import MessageBoxBase


class NameInputDialog(MessageBoxBase):
    """Fluent name editor with inline validation and disabled confirmation."""

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
        super().__init__(parent)
        self.name_validator = name_validator
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(
            SubtitleLabel(self.tr(title), self), alignment=title_alignment
        )

        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText(self.tr(placeholder))
        self.name_edit.setText(name)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.textChanged.connect(self._validate_name)
        self.viewLayout.addWidget(self.name_edit)

        self.name_error_label = CaptionLabel(error_message, self)
        self.name_error_label.setTextColor(QColor("#FF0000"), QColor("#FF0000"))
        self.name_error_label.setVisible(False)
        self.viewLayout.addWidget(self.name_error_label)

        self.yesButton.setText(self.tr("OK"))
        self.cancelButton.setText(self.tr("取消"))
        self.widget.setMinimumWidth(320)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self._validate_name(name)

    def _validate_name(self, name: str) -> None:
        normalized_name = name.strip()
        is_valid = bool(normalized_name) and (
            self.name_validator is None or self.name_validator(normalized_name)
        )
        self.yesButton.setEnabled(is_valid)
        self.name_error_label.setVisible(
            bool(normalized_name) and not is_valid and bool(self.name_error_label.text())
        )

    def validate(self) -> bool:
        self._validate_name(self.name_edit.text())
        return self.yesButton.isEnabled()

    def get_name(self) -> str:
        return self.name_edit.text().strip()

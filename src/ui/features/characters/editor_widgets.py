"""Character editor visuals that do not replace the kit's widget styles."""

from html import escape

from ok.ui.qt.kit import Label, theme
from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.ui.foundation.widgets.code_editor import CodeEditor


class CharacterTitleLabel(Label):
    """Keep the title's typography inside QLabel's text layout."""

    def __init__(self, text="", parent=None):
        super().__init__(text, "display", parent)
        self.setTextFormat(Qt.TextFormat.RichText)

    def setText(self, text):
        self._plain_text = text
        super().setText(
            '<span style="font-size: 28px; font-weight: 600; white-space: pre-wrap;">'
            f"{escape(text)}</span>"
        )

    def text(self):
        return self._plain_text


class ComboEditorFrame(QWidget):
    """Paint the combo editor's surface around an unchanged code editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = CodeEditor(self)
        self.editor.setStyleSheet("QPlainTextEdit { background: transparent; border: 0; }")
        self.editor.installEventFilter(self)
        self.setFocusProxy(self.editor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(140)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.editor)
        theme.changed.connect(self.update)

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(theme.color("surface_alt"))
        color = theme.accent() if self.editor.hasFocus() else theme.color("border_strong")
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7.5, 7.5)

"""Shared code editor with indentation and comment shortcuts."""

from ok.ui.qt.kit import theme
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    indent = "    "

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codeEditor")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.line_number_area = LineNumberArea(self)
        self.error_line_number = -1
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.line_number_area.update)
        theme.changed.connect(self.line_number_area.update)
        self.update_line_number_area_width()

    def line_number_area_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 20 + self.fontMetrics().horizontalAdvance("9") * max(digits, 3)

    def update_line_number_area_width(self, _count=0):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if number + 1 == self.error_line_number:
                    color = "danger"
                else:
                    color = "text_secondary" if number == current else "text_disabled"
                painter.setPen(theme.color(color))
                painter.drawText(
                    0,
                    int(top),
                    self.line_number_area.width() - 12,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            number += 1

    def set_error_line(self, line_number):
        self.error_line_number = line_number
        self.line_number_area.update()

    def keyPressEvent(self, event):
        if self.isReadOnly():
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        if (
            event.key() == Qt.Key.Key_Slash
            and modifiers & Qt.KeyboardModifier.ControlModifier
            and not modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            self._toggle_comment()
            return
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not modifiers & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            if event.key() == Qt.Key.Key_Backtab or modifiers & Qt.KeyboardModifier.ShiftModifier:
                self._unindent()
            else:
                self._indent()
            return
        super().keyPressEvent(event)

    def _toggle_comment(self):
        cursor = self.textCursor()
        positions = self._block_positions(cursor)
        lines = []
        for line_start in positions:
            line = self.document().findBlock(line_start).text()
            indent = len(line) - len(line.lstrip(" \t"))
            if line[indent:].strip():
                content = line[indent:]
                marker_length = 1 + int(content.startswith("# "))
                lines.append((line_start + indent, content.startswith("#"), marker_length))

        if not lines:
            return

        uncomment = all(commented for _, commented, _ in lines)
        changes = []
        if uncomment:
            for position, _, marker_length in lines:
                changes.append((position, marker_length, ""))
        else:
            changes.extend((position, 0, "# ") for position, commented, _ in lines if not commented)

        anchor, position = cursor.anchor(), cursor.position()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for change_position, removed, inserted in reversed(changes):
            edit.setPosition(change_position)
            if removed:
                edit.setPosition(change_position + removed, QTextCursor.MoveMode.KeepAnchor)
                edit.removeSelectedText()
            if inserted:
                edit.insertText(inserted)
        edit.endEditBlock()

        def adjusted(offset):
            delta = 0
            for change_position, removed, inserted in changes:
                if inserted and offset >= change_position:
                    delta += len(inserted)
                if removed and offset > change_position:
                    delta -= min(removed, offset - change_position)
            return offset + delta

        selection = QTextCursor(self.document())
        selection.setPosition(adjusted(anchor))
        selection.setPosition(adjusted(position), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)

    def _block_positions(self, cursor):
        document = self.document()
        first = document.findBlock(
            cursor.selectionStart() if cursor.hasSelection() else cursor.position()
        )
        last = document.findBlock(
            cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
        )
        if cursor.hasSelection() and cursor.selectionEnd() == last.position():
            last = last.previous()
        last_number = last.blockNumber()
        positions = []
        block = first
        while block.isValid() and block.blockNumber() <= last_number:
            positions.append(block.position())
            block = block.next()
        return positions

    def _indent(self):
        cursor = self.textCursor()
        if not cursor.hasSelection():
            spaces = 4 - cursor.positionInBlock() % 4
            cursor.insertText(" " * spaces)
            self.setTextCursor(cursor)
            return

        positions = self._block_positions(cursor)
        anchor, position = cursor.anchor(), cursor.position()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for line_start in reversed(positions):
            edit.setPosition(line_start)
            edit.insertText(self.indent)
        edit.endEditBlock()

        new_anchor = anchor + len(self.indent) * sum(
            line_start <= anchor for line_start in positions
        )
        new_position = position + len(self.indent) * sum(
            line_start <= position for line_start in positions
        )
        selection = QTextCursor(self.document())
        selection.setPosition(new_anchor)
        selection.setPosition(new_position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)

    def _unindent(self):
        cursor = self.textCursor()
        positions = self._block_positions(cursor)
        changes = []
        for line_start in positions:
            block = self.document().findBlock(line_start)
            line = block.text()
            if line.startswith("\t"):
                changes.append((line_start, 1))
            else:
                spaces = min(len(self.indent), len(line) - len(line.lstrip(" ")))
                if spaces:
                    changes.append((line_start, spaces))

        if not changes:
            return

        anchor, position = cursor.anchor(), cursor.position()
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for line_start, length in reversed(changes):
            edit.setPosition(line_start)
            edit.setPosition(line_start + length, QTextCursor.MoveMode.KeepAnchor)
            edit.removeSelectedText()
        edit.endEditBlock()

        def adjusted(offset):
            removed = 0
            for line_start, length in changes:
                if offset <= line_start:
                    break
                removed += min(length, offset - line_start)
            return offset - removed

        selection = QTextCursor(self.document())
        selection.setPosition(adjusted(anchor))
        selection.setPosition(adjusted(position), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selection)

"""Layout and placement for the project's rich-text help popovers."""

from ok.ui.qt.kit import Label, Popover
from PySide6.QtCore import QEvent, QPoint, QTimer
from PySide6.QtWidgets import QVBoxLayout


class HelpPopover(Popover):
    def __init__(self, parent, title, content, *, open_external_links=False):
        super().__init__(parent)
        self._help_anchor = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.addWidget(Label(title, "strong", self))
        self._body = Label(content, "body", self, word_wrap=True)
        self._body.setOpenExternalLinks(open_external_links)
        layout.addWidget(self._body)

    def toggle_for(self, anchor):
        if self.isVisible():
            self.close_popover()
            return

        self._help_anchor = anchor
        self._place_by_anchor(anchor)

    def _place_by_anchor(self, anchor):
        window = anchor.window()
        layout = self.layout()
        self._body.setWordWrap(False)
        natural_width = layout.totalSizeHint().width()
        self._body.setWordWrap(True)
        self.setFixedWidth(min(natural_width, 552, max(1, window.width() - 32)))
        height = layout.totalHeightForWidth(self.width())
        anchor_y = anchor.mapTo(window, QPoint()).y()
        below = window.height() - anchor_y - anchor.height() - 16
        above = height > below and anchor_y - 16 > below
        self.popup(anchor, above=above)

    def eventFilter(self, watched, event):
        if (
            watched is self.parentWidget()
            and event.type() == QEvent.Type.Resize
            and self.isVisible()
        ):
            # Let the window lay out the anchor before measuring its new position.
            QTimer.singleShot(0, self, self._reposition_after_resize)
        return super().eventFilter(watched, event)

    def _reposition_after_resize(self):
        if self.isVisible() and self._help_anchor is not None:
            self._place_by_anchor(self._help_anchor)

from ok.ui.qt.kit import TabStrip, theme
from PySide6.QtCore import QRect

from src.ui.foundation.widgets.separator import paint_separator


class PageTabStrip(TabStrip):
    """Underline page tabs with a continuous, theme-aware baseline."""

    def __init__(self, parent=None, *, stable_label_width=False, label_size=None):
        super().__init__(look="underline", parent=parent)
        theme.changed.connect(self.update)

        label_style = "font-weight: 600; " if stable_label_width else ""
        if label_size is not None:
            label_style += f"font-size: {label_size}px;"
        style = 'PageTabStrip[look="underline"] { border-bottom: 0; }'
        if label_style:
            style += f"TabItem QLabel#tabText {{ {label_style} }}"
        self.setStyleSheet(style)
        # Keep room at the bottom edge for the kit's selected indicator.
        self.layout().setContentsMargins(0, 0, 0, 1)

    def paintEvent(self, event):
        paint_separator(self, QRect(0, self.height() - 2, self.width(), 2))
        super().paintEvent(event)

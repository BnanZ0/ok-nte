"""Project styling for lightweight actions that read as links."""

from ok.ui.qt.kit import Button, icon, theme


class LinkButton(Button):
    def __init__(self, text, *, parent=None, icon_name=None, size="normal"):
        super().__init__(
            text,
            icon=icon(icon_name, "accent") if icon_name else None,
            variant="subtle",
            size=size,
            parent=parent,
        )
        theme.changed.connect(self._refresh_accent)
        self._refresh_accent()

    def _refresh_accent(self):
        self.setStyleSheet(f"QLabel#buttonText:enabled {{ color: {theme.accent().name()}; }}")

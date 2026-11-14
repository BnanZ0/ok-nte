"""Small locale helpers shared by UI surfaces."""

from ok import og
from PySide6.QtCore import QCoreApplication


def translate(text: str) -> str:
    """Translate project-owned content independently of the Qt widget class."""
    app = og.app
    return app.tr(text) if app is not None else QCoreApplication.translate("app", text)


def is_chinese() -> bool:
    """Return whether the active application locale is any Chinese locale."""
    try:
        return bool(og.app and "zh" in og.app.locale.name())
    except Exception:
        return False

"""Load project-owned icon artwork from a shared resource directory."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

_APP_ROOT = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[2]
)
_FEATURE_ICON_FOLDER = _APP_ROOT / "icons" / "features"
_TASK_ICON_FOLDER = _APP_ROOT / "icons" / "tasks"


def _icon_path(name: str, folder: Path) -> str | None:
    for suffix in ("svg", "png"):
        path = folder / f"{name}.{suffix}"
        if path.is_file():
            return str(path)
    return None


def feature_icon(name: str) -> str | None:
    """Resolve project artwork for a software feature, preferring SVG over PNG."""
    return _icon_path(name, _FEATURE_ICON_FOLDER)


def task_icon(name: str) -> str | None:
    """Resolve project artwork for an automation task, preferring SVG over PNG."""
    return _icon_path(name, _TASK_ICON_FOLDER)


def resource_icon(name: str, *, color: str = "text") -> QIcon:
    """Load monochrome artwork for a widget using a kit theme color."""
    from ok.ui.qt.kit import load_icon
    from ok.ui.qt.kit.icons import tinted_icon

    return tinted_icon(load_icon(feature_icon(name)), color)

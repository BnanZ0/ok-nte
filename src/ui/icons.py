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
_ICON_FOLDER = _APP_ROOT / "icons" / "tasks"


def task_icon(name: str) -> str:
    """Resolve project artwork for a task card, preferring SVG over PNG."""
    for suffix in ("svg", "png"):
        path = _ICON_FOLDER / f"{name}.{suffix}"
        if path.is_file():
            return str(path)
    raise FileNotFoundError(f"No SVG or PNG for task icon {name!r} in {_ICON_FOLDER}")


def resource_icon(name: str, *, color: str = "text") -> QIcon:
    """Load monochrome artwork for a widget using a kit theme color."""
    from ok.ui.qt.kit import load_icon
    from ok.ui.qt.kit.icons import tinted_icon

    return tinted_icon(load_icon(task_icon(name)), color)

"""Structured description of a window variant's audio capture target.

Deliberately dependency-free: ``SoundListener`` re-resolves this on every loop
iteration, so importing it must not pull in numpy/librosa, the GUI layer, or
the device manager.
"""
from __future__ import annotations

from dataclasses import dataclass

KIND_PROCESS_LOOPBACK = "process_loopback"


@dataclass(frozen=True)
class AudioCaptureSpec:
    """Immutable value object compared by the listener's reconcile loop."""

    kind: str = KIND_PROCESS_LOOPBACK
    process_names: tuple[str, ...] = ()
    include_process_tree: bool = True

    @property
    def factory_process_name(self) -> str | tuple[str, ...]:
        """Argument for the existing ``create_capture_source(process_name=...)``.

        A single name stays a ``str`` so the current factory signature is
        untouched. ``process_resolver.name_set()`` already accepts collections,
        so a browser variant listing several processes needs no factory change.
        """
        names = tuple(str(name) for name in self.process_names if name)
        if not names:
            return ""
        return names[0] if len(names) == 1 else names

    @property
    def display(self) -> str:
        names = ", ".join(self.process_names) or "<none>"
        tree = "tree" if self.include_process_tree else "no-tree"
        return f"{self.kind}[{names}] ({tree})"

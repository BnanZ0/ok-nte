from __future__ import annotations

from typing import Optional

from src.sound_trigger.capture.base import AudioCaptureSource
from src.sound_trigger.capture.process_loopback import ProcessLoopbackSource

MODE_PROCESS = "process"


def create_capture_source(
    mode: str = MODE_PROCESS,
    *,
    process_name: Optional[str] = None,
    hwnd: int | None = None,
) -> AudioCaptureSource:
    if mode != MODE_PROCESS:
        raise ValueError(f"Unsupported audio capture mode: {mode}")
    if not process_name:
        raise ValueError("process_name is required for WASAPI process loopback")
    return ProcessLoopbackSource(process_name=process_name, hwnd=hwnd)


__all__ = [
    "AudioCaptureSource",
    "ProcessLoopbackSource",
    "create_capture_source",
    "MODE_PROCESS",
]

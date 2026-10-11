from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from ok import og
from ok.core.events import EventMessage, EventSignal, communicate

_PROJECT_EVENT_NAMES = (
    "confirmation_requested",
    "overlay_shown",
    "overlay_cleared",
)


def _forward_event(name: str):
    def forward(*args: Any, **kwargs: Any) -> None:
        communicate.any.emit(EventMessage(name, args, kwargs))

    return forward


def _install_project_events() -> None:
    """Add project-owned named signals while preserving EventBus forwarding."""
    for name in _PROJECT_EVENT_NAMES:
        if not hasattr(communicate, name):
            setattr(communicate, name, EventSignal(name, _forward_event(name)))


_install_project_events()


class BotNotificationMessage(str):
    """Plain notification text explicitly allowed to reach external providers."""


@dataclass(frozen=True)
class RecordingMarker:
    index: int
    x: float
    y: float


class OverlayContent:
    """Base type for data rendered by the shared application overlay."""


@dataclass(frozen=True)
class RecordingOverlayContent(OverlayContent):
    markers: tuple[RecordingMarker, ...]
    instruction: str


@dataclass(frozen=True)
class OverlayShown:
    key: str
    content: OverlayContent


@dataclass(frozen=True)
class OverlayCleared:
    key: str


@dataclass
class ConfirmationRequested:
    title: str
    content: str
    rich_text: bool = False
    copyable: bool = False
    hide_cancel: bool = False
    close_delay_seconds: float | None = None
    _response: Future[bool] = field(default_factory=Future, init=False, repr=False)

    def resolve(self, accepted: bool) -> None:
        if not self._response.done():
            self._response.set_result(accepted)

    def wait_for_response(self) -> bool:
        if getattr(og, "main_window", None) is None:
            return False
        return self._response.result()

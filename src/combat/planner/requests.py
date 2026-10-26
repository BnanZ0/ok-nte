from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .types import (
    NEVER_EXPIRES,
    ActionResult,
    ActionTag,
    Planner,
    RequestHandle,
    RequestStatus,
    Role,
    _Action,
    _NeverExpires,
    _RouteStep,
)

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar


RequestDeadline = Callable[[], bool]
RequestLifetime = RequestDeadline | _NeverExpires


@dataclass(slots=True)
class _RequestLifetime:
    """planner 内部请求的共同生命周期字段。"""

    reason: str
    _source: int
    until: RequestLifetime | None = None
    return_to_source: bool = False
    on_finish: Callable[[], None] | None = None
    handle: RequestHandle = field(default_factory=RequestHandle)

    parent: _WindowRequest | None = field(default=None, repr=False)

    def expired(self, now: float) -> bool:
        if self.handle.is_expired or (self.parent is not None and self.parent.handle.is_closed):
            return True
        if self.until is None or self.until is NEVER_EXPIRES:
            return False
        return self.until()

    def fulfilled(self) -> bool:
        return False

    def finish(self, status: RequestStatus) -> None:
        self.handle._finish(status)

    def close(self) -> None:
        self.handle._close()


@dataclass(slots=True)
class _RouteRequest(_RequestLifetime):
    steps: list[_RouteStep] = field(default_factory=list)
    _progress: int = 0
    _selected_action: str | None = field(default=None, repr=False)

    def fulfilled(self) -> bool:
        return self._progress >= len(self.steps)

    def current_step(self) -> _RouteStep | None:
        if not self.steps or self.fulfilled():
            return None
        return self.steps[self._progress]

    def wants(self, char: "BaseChar", action: _Action) -> bool:
        step = self.current_step()
        return step.wants(char, action) if step is not None else False

    def wants_entry_reaction(self, source_char: "BaseChar", target_char: "BaseChar") -> bool:
        step = self.current_step()
        return step.wants_entry_reaction(source_char, target_char) if step is not None else False

    def complete_step(self, char: "BaseChar", action: _Action | ActionResult) -> bool:
        step = self.current_step()
        if step is None:
            return False
        if isinstance(action, ActionResult) and not action.success:
            return False
        if self._selected_action is not None and (
            isinstance(action, ActionResult) or action.identity_key() != self._selected_action
        ):
            return False
        if not step.wants(char, action):
            return False
        self._progress += 1
        self._selected_action = None
        return True

    def skip_current_step(self) -> bool:
        step = self.current_step()
        if step is None or not step.optional:
            return False
        self._progress += 1
        self._selected_action = None
        return True

    def complete_switch(self, target_char: "BaseChar") -> bool:
        step = self.current_step()
        if (
            step is None
            or not step.switch_step
            or step.wait_for_turn
            or not step.matches_char(target_char)
        ):
            return False
        self._progress += 1
        return True

    def complete_turn(self, char: "BaseChar") -> bool:
        step = self.current_step()
        if (
            step is None
            or not step.switch_step
            or not step.wait_for_turn
            or not step.matches_char(char)
        ):
            return False
        self._progress += 1
        return True

    def complete_entry_reaction(self, source_char: "BaseChar", target_char: "BaseChar") -> bool:
        step = self.current_step()
        if step is None or not step.wants_entry_reaction(source_char, target_char):
            return False
        self._progress += 1
        return True


@dataclass(slots=True)
class _ReservationRequest(_RequestLifetime):
    reservations: list[Planner.Reserve] = field(default_factory=list)

    def reserves_action(self, char: "BaseChar", action: _Action | ActionResult) -> bool:
        if char.index == self._source:
            return False
        return any(reservation.reserves_action(char, action) for reservation in self.reservations)


@dataclass(slots=True)
class _WindowRequest(_RequestLifetime):
    """Independent lifetime; it does not score actions or interrupt entry."""


@dataclass(slots=True)
class _SwitchRequest(_RequestLifetime):
    target_index: int = -1

    def target_from(self, chars: list["BaseChar"]) -> "BaseChar | None":
        for char in chars:
            if char is not None and char.index == self.target_index:
                return char
        return None

    def fulfilled_by(self, char: "BaseChar") -> bool:
        return char.index == self.target_index


@dataclass(slots=True)
class _RoleRequest(_RequestLifetime):
    role: Role = Role.SUB_DPS
    _describe: Callable | None = field(default=None, repr=False)

    def matches(self, char: "BaseChar") -> bool:
        profile = self._describe(char) if self._describe is not None else char.describe_role()
        return profile.role == self.role


@dataclass(slots=True)
class _TagRequest(_RequestLifetime):
    required_tags: set[ActionTag] = field(default_factory=set)
    count: int = 1
    avoid_source: bool = True
    _completed_chars: set[int] = field(default_factory=set)

    def fulfilled(self) -> bool:
        return len(self._completed_chars) >= self.count

    def wants(self, char: "BaseChar", action: _Action) -> bool:
        if self.avoid_source and char.index == self._source and not self.fulfilled():
            return False
        if char.index in self._completed_chars and not self.fulfilled():
            return False
        return bool(self.required_tags.intersection(action.tags))

    def complete_step(self, char: "BaseChar", action: _Action | ActionResult) -> bool:
        if self.avoid_source and char.index == self._source:
            return False
        if isinstance(action, ActionResult) and not action.success:
            return False
        if not self.required_tags.intersection(action.tags):
            return False
        self._completed_chars.add(char.index)
        return True


_Request = (
    _RouteRequest
    | _ReservationRequest
    | _SwitchRequest
    | _RoleRequest
    | _TagRequest
    | _WindowRequest
)


def request_fulfilled(request: _Request) -> bool:
    """判断请求是否完成；reservation 没有完成态。"""

    if isinstance(request, (_RouteRequest, _TagRequest)):
        return request.fulfilled()
    return False


def request_has_expiration(request: _Request) -> bool:
    """判断请求 fulfilled 后是否仍需要等待生命周期过期信号。"""

    return (
        not isinstance(request, _RouteRequest)
        and request.until is not None
        and request.until is not NEVER_EXPIRES
    )


def request_wants_action(request: _Request, char: "BaseChar", action: _Action) -> bool:
    """判断请求是否需要某角色动作。"""

    if isinstance(request, (_RouteRequest, _TagRequest)):
        return request.wants(char, action)
    return False


def request_complete_action(
    request: _Request,
    char: "BaseChar",
    action: _Action | ActionResult,
) -> bool:
    """用一次动作推进请求；reservation 不参与完成进度。"""

    if isinstance(request, (_RouteRequest, _TagRequest)):
        return request.complete_step(char, action)
    return False


def request_reserves_action(
    request: _Request,
    char: "BaseChar",
    action: _Action | ActionResult,
) -> bool:
    """判断请求是否保留某角色动作；当前只有 reservation 支持。"""

    if isinstance(request, _ReservationRequest):
        return request.reserves_action(char, action)
    return False


def request_switch_targets(request: _Request, chars: list["BaseChar"]) -> list["BaseChar"]:
    """返回纯切人 request 的候选角色；非切人请求返回空列表。"""

    if isinstance(request, _SwitchRequest):
        target = request.target_from(chars)
        return [target] if target is not None else []
    if isinstance(request, _RoleRequest):
        return [char for char in chars if char is not None and request.matches(char)]
    return []


def request_is_switch(request: _Request) -> bool:
    """判断请求是否是纯切人请求。"""

    return isinstance(request, (_SwitchRequest, _RoleRequest))


def request_complete_switch(request: _Request, target_char: "BaseChar") -> bool:
    """判断一次实际切人是否消费了 switch request。"""

    if isinstance(request, _SwitchRequest):
        return request.fulfilled_by(target_char)
    if isinstance(request, _RoleRequest):
        return request.matches(target_char)
    return False


def request_counts_as_active(request: _Request) -> bool:
    """判断请求是否应被角色视为正在进行的协作需求。

    纯 reservation 和切人 request 都不代表“当前动作链要让路”；它们只分别
    提供动作许可限制和下一次普通切人偏好。
    """

    return isinstance(request, (_RouteRequest, _TagRequest)) and (
        not request_fulfilled(request) or request.return_to_source
    )


def request_blocks_entry_flow(request: _Request) -> bool:
    """判断请求是否需要打断当前角色的入场动作流。

    纯 reservation 只负责禁止指定动作，不代表 planner 需要重新调度，因此不应
    阻止当前角色继续尝试下一个 allowed action。
    """

    return request_counts_as_active(request)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from ok import Logger

from src.utils import clock

from .requests import (
    _Request,
    _RouteRequest,
    request_complete_action,
    request_complete_entry_reaction,
    request_complete_switch,
    request_fulfilled,
    request_has_expiration,
)
from .types import (
    ActionIntent,
    ActionResult,
    EntryFactory,
    ExpectedEntry,
    FieldClaim,
    RequestStatus,
    _display_result_name,
)

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar


logger = Logger.get_logger("planner")


@dataclass(slots=True)
class CombatState:
    """`CombatPlanner` 的运行状态容器。

    保存队伍角色、协作请求、严格路线锁以及切入动作期望。通常由
    `CombatPlanner` 管理，角色代码不应直接修改。
    """

    chars: list["BaseChar"] = field(default_factory=list)
    active_requests: list[_Request] = field(default_factory=list)
    lifecycle_requests: list[_Request] = field(default_factory=list)
    locked_route: _RouteRequest | None = None
    pending_entry_expectations: dict[int, ExpectedEntry] = field(default_factory=dict)

    def reset(self, chars: Iterable["BaseChar"]) -> None:
        """用当前队伍重置 planner 状态。"""

        for request in self.active_requests:
            request.close()
        for request in self.lifecycle_requests:
            request.close()
        if self.locked_route is not None:
            self.locked_route.close()
        self.chars = [char for char in chars if char is not None]
        self.active_requests.clear()
        self.lifecycle_requests.clear()
        self.locked_route = None
        self.pending_entry_expectations.clear()

    def prune(self) -> None:
        """清理过期请求，并处理 strict route 的过期锁定行为。"""

        now = clock.now()
        self.expire_locked_route(now)
        active_requests = []
        for request in self.active_requests:
            if request.expired(now):
                request.finish(RequestStatus.EXPIRED)
                request.close()
                continue
            active_requests.append(request)
        self.active_requests = active_requests
        lifecycle_requests = []
        for request in self.lifecycle_requests:
            if request.expired(now):
                request.finish(RequestStatus.EXPIRED)
                request.close()
                continue
            lifecycle_requests.append(request)
        self.lifecycle_requests = lifecycle_requests

    def expire_locked_route(self, now: float | None = None) -> bool:
        """仅检查当前 route 的过期条件, 返回是否已按过期规则结束。"""

        if now is None:
            now = clock.now()
        if self.locked_route is not None and self.locked_route.expired(now):
            step = self.locked_route.current_step()
            step_reason = step.reason if step is not None else "route completed"
            logger.warning(
                f"strict route deadline expired, route unlocked: "
                f"{self.locked_route.reason} / {step_reason}"
            )
            self.locked_route.finish(RequestStatus.EXPIRED)
            self.locked_route.close()
            self.locked_route = None
            return True
        return False

    def add_requests(self, requests: Iterable[_Request]) -> None:
        """加入角色新发布的协作请求。"""

        for request in requests:
            if request_fulfilled(request) and not request.return_to_source:
                request.finish(RequestStatus.FULFILLED)
                request.close()
                if request_has_expiration(request):
                    self.lifecycle_requests.append(request)
                continue
            if isinstance(request, _RouteRequest) and request.steps:
                if self.locked_route is not None:
                    logger.info(f"strict route replaced: {self.locked_route.reason}")
                    self.locked_route.finish(RequestStatus.EXPIRED)
                    self.locked_route.close()
                self.locked_route = request
                step = request.current_step()
                step_reason = step.reason if step is not None else "no step"
                logger.info(f"strict route locked: {request.reason} / {step_reason}")
                continue
            self.active_requests.append(request)

    def record_action(self, char: "BaseChar", result: ActionResult) -> None:
        """记录一次角色动作，并推进相关协作请求。"""

        for request in self.active_requests:
            request_complete_action(request, char, result)
            if request_fulfilled(request):
                request.finish(RequestStatus.FULFILLED)
        self.active_requests = self._keep_active_after_progress(char.index)
        if self.locked_route is None:
            return

        step = self.locked_route.current_step()
        if self.locked_route.complete_step(char, result):
            result_name = _display_result_name(result)
            step_reason = step.reason if step is not None else result_name
            logger.info(
                f"strict route completed step {char}: "
                f"{self.locked_route.reason} / {step_reason} -> {result_name}"
            )
            if self.locked_route.fulfilled():
                self.fulfill_locked_route()

    def record_entry_reaction(self, source_char: "BaseChar", target_char: "BaseChar") -> None:
        """记录一次入场/环合反应，并推进相关协作请求。"""

        for request in self.active_requests:
            request_complete_entry_reaction(request, source_char, target_char)
            if request_fulfilled(request):
                request.finish(RequestStatus.FULFILLED)
        self.active_requests = self._keep_active_after_progress(target_char.index)
        if self.locked_route is None:
            return

        step = self.locked_route.current_step()
        if self.locked_route.complete_entry_reaction(source_char, target_char):
            step_reason = step.reason if step is not None else "entry reaction"
            logger.info(
                f"strict route completed entry reaction {source_char} -> {target_char}: "
                f"{self.locked_route.reason} / {step_reason}"
            )
            if self.locked_route.fulfilled():
                self.fulfill_locked_route()

    def record_switch(self, target_char: "BaseChar") -> None:
        """记录一次实际切人，并消费匹配的 switch request。"""

        active_requests = []
        for request in self.active_requests:
            if request_complete_switch(request, target_char):
                request.finish(RequestStatus.FULFILLED)
                request.close()
                logger.info(f"switch request fulfilled: {request.reason}")
                continue
            active_requests.append(request)
        self.active_requests = active_requests
        self.complete_route_arrival_steps(target_char)

    def complete_route_arrival_steps(self, target_char: "BaseChar") -> None:
        """完成目标已在场的纯切人步骤, 包括连续指向同一角色的步骤。"""

        while self.locked_route is not None:
            if not self.locked_route.complete_switch(target_char):
                return
            if self.locked_route.fulfilled():
                self.fulfill_locked_route()

    def fulfill_locked_route(self) -> None:
        """完成当前 strict route，并按配置清除或转为返回发起者请求。"""

        if self.locked_route is None:
            return
        self.locked_route.finish(RequestStatus.FULFILLED)
        if not self.locked_route.return_to_source:
            logger.info(f"strict route fulfilled: {self.locked_route.reason}")
            self.locked_route.close()
            if request_has_expiration(self.locked_route):
                self.lifecycle_requests.append(self.locked_route)
            self.locked_route = None
            return
        self.active_requests.append(self.locked_route)
        logger.info(f"strict route fulfilled: {self.locked_route.reason}")
        self.locked_route = None

    def _keep_active_after_progress(self, actor_index: int) -> list[_Request]:
        active_requests = []
        for request in self.active_requests:
            if not request_fulfilled(request):
                active_requests.append(request)
                continue
            if request.return_to_source and actor_index != request._source:
                active_requests.append(request)
                continue
            if request_has_expiration(request):
                if request.return_to_source and actor_index == request._source:
                    request.return_to_source = False
                self.lifecycle_requests.append(request)
            request.close()
        return active_requests

    def char_by_index(self, index: int) -> "BaseChar | None":
        """按队伍 index 查找角色。"""

        for char in self.chars:
            if char.index == index:
                return char
        return None

    def set_pending_entry_expectation(
        self, char: "BaseChar", expected_entry: ExpectedEntry | None
    ) -> None:
        """登记某角色下次切入后的动作期望; None 清除旧期望。"""

        if expected_entry is None:
            self.pending_entry_expectations.pop(char.index, None)
        else:
            self.pending_entry_expectations[char.index] = expected_entry

    def pop_pending_entry_expectation(self, char: "BaseChar") -> ExpectedEntry | None:
        """取出并清除某角色的切入动作期望。"""

        return self.pending_entry_expectations.pop(char.index, None)


@dataclass(slots=True)
class _PlanSnapshot:
    """一次 planner 决策内缓存的角色 plan 快照。"""

    actions: list[ActionIntent] = field(default_factory=list)
    claims: list[FieldClaim] = field(default_factory=list)
    entry: EntryFactory | None = None

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .requests import (
    RequestDeadline,
    RequestLifetime,
    _Request,
    _ReservationRequest,
    _RoleRequest,
    _RouteRequest,
    _SwitchRequest,
    _TagRequest,
    request_counts_as_active,
    request_reserves_action,
)
from .state import CombatState, _PlanSnapshot
from .types import (
    NEVER_EXPIRES,
    ActionIntent,
    ActionReservation,
    ActionResult,
    ActionSlot,
    ActionTag,
    FollowupStep,
    RequestHandle,
    Role,
)

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask


@dataclass(slots=True)
class CombatContext:
    """角色动作执行时收到的 planner 上下文。

    `ActionIntent.execute` 会收到此对象。角色可用它查询 strict route、检查
    planner 是否允许某槽位动作、或向 `CombatPlanner` 发布协作请求。
    """

    task: "BaseCombatTask"
    _state: CombatState
    current_char: "BaseChar"
    _published_requests: list[_Request] = field(default_factory=list)
    _plan_cache: dict[int, _PlanSnapshot] | None = None

    @property
    def chars(self) -> list["BaseChar"]:
        """当前 planner 管理的队伍角色列表。"""

        return self._state.chars

    def has_active_request(self) -> bool:
        """返回当前是否存在未完成的协作请求或 strict route。"""

        self._state.prune()
        return bool(
            self._state.locked_route
            or any(request_counts_as_active(request) for request in self._state.active_requests)
        )

    def has_strict_route(self) -> bool:
        """返回当前是否存在正在锁定执行的 strict route。"""

        self._state.prune()
        return self._state.locked_route is not None

    def strict_route_wants_action(
        self,
        char: "BaseChar",
        slot: ActionSlot | None = None,
        action_name: str = "",
        tags: set[ActionTag] | None = None,
    ) -> bool:
        """查询当前 strict route 是否正在请求指定角色动作。

        Args:
            char: 要检查的角色。
            slot: 动作槽位；普通协作优先使用此参数，例如 `ActionSlot.SKILL`。
            action_name: 高级精确匹配用的动作名；普通角色通常不需要。
            tags: 动作标签集合；只在 route step 用 tag 匹配时需要。

        Returns:
            如果当前 strict route 的当前步骤想要该角色执行这个动作，返回 True。
        """

        request = self._state.locked_route
        if request is None:
            return False
        step = request.current_step()
        if step is None:
            return False
        action = ActionResult(name=action_name, tags=set(tags or set()), slot=slot)
        return step.wants(char, action)

    def is_slot_available(
        self,
        char: "BaseChar",
        slot: ActionSlot | None = None,
        action_name: str = "",
        tags: set[ActionTag] | None = None,
    ) -> bool:
        """查询 planner 是否允许指定角色的原始动作执行。

        只检查 strict route 和 reservation; 不检查 `ActionIntent.can_execute`。

        Args:
            char: 准备执行动作的角色。
            slot: 动作槽位。设置了 slot 的 `ActionIntent` 会由 planner 自动检查，
                手写长动作时才需要主动调用。
            action_name: 动作名；用于高级精确匹配。
            tags: 动作标签集合；用于 tag request 或特殊匹配。

        Returns:
            True 表示 planner 没有 reservation 阻止此动作，或当前 strict route
            正在要求此动作；False 表示动作被其他角色的 reservation 保留。
        """

        self._state.prune()
        action = ActionResult(name=action_name, tags=set(tags or set()), slot=slot)
        request = self._state.locked_route
        if request is not None:
            step = request.current_step()
            if step is not None and step.wants(char, action):
                return True
        for active_request in self._state.active_requests:
            if request_reserves_action(active_request, char, action):
                return False
        return True

    def is_action_allowed(self, char: "BaseChar", action: ActionIntent) -> bool:
        """返回 planner 是否允许指定角色执行完整 action 声明。

        同时检查 action 自身的 `can_execute` 限制和 slot reservation。角色代码
        需要在 entry flow 外预查询动作时可以使用此方法; 普通执行仍应直接
        `yield action`，由 planner 在执行前统一检查。
        """

        if not action.is_allowed(self):
            return False
        if action.slot is None:
            return True
        return self.is_slot_available(
            char,
            slot=action.slot,
            action_name=action.name,
            tags=set(action.tags),
        )

    def request_route(
        self,
        steps: FollowupStep | list[FollowupStep],
        reason: str = "",
        until: RequestDeadline | None = None,
        return_to_source: bool = False,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """发布固定顺序协作路线。

        route 是 strict request：planner 会按 `steps` 顺序调度目标角色，并尽量让
        每一步指定的动作先执行。route 只表达“谁按什么顺序做什么”，不保留窗口内
        其他动作；窗口保留请单独搭配 `reserve_actions()`。

        Args:
            steps: 单个 FollowupStep 或按执行顺序排列的步骤列表。
                支持指定动作、入场反应或 for_switch() 切人步骤。
                for_switch() 默认等待目标执行完一轮正常出招流程后再推进路线。
                列表为空时不会发布请求, 并返回 None。
            reason: 日志和调试用理由；为空时会自动使用当前角色生成默认理由。
            until: 过期条件。为 None 时不会因时间/机制条件过期；传 callable 时，
                每次 planner prune 都会调用，返回 True 则 route 以 EXPIRED 结束。
            return_to_source: route 成功完成后，是否提高发起者重新入场的优先级。
            on_finish: route 首次出现 FULFILLED 或 EXPIRED 信号时调用一次。
                需要分别处理成功和过期时，保存返回的 handle 后使用
                `handle.on_fulfilled(...)` / `handle.on_expired(...)`。

        Returns:
            `RequestHandle`，可用于查询 route 状态，或把 `handle.when.*`
            传给其他 request 的 `until=`。
        """

        if isinstance(steps, FollowupStep):
            steps = [steps]
        if not steps:
            return None
        request = _RouteRequest(
            reason=reason or f"{self.current_char} route request",
            _source=self.current_char.index,
            until=until,
            return_to_source=return_to_source,
            on_finish=on_finish,
            steps=steps,
        )
        self._publish_request(request)
        return request.handle

    def reserve_actions(
        self,
        reservations: list[ActionReservation],
        reason: str = "",
        *,
        until: RequestLifetime,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """发布纯动作保留请求。

        reservation 只负责阻止指定队友动作被普通流程提前消耗。它不推进 route，
        不代表请求完成，也不会主动要求目标角色执行动作。

        Args:
            reservations: 要保留的动作集合。每个 `ActionReservation` 描述目标角色
                和被保留的 slot。为空时不会发布请求，并返回 None。
            reason: 日志和调试用理由；为空时会自动使用当前角色生成默认理由。
            until: 必填生命周期。传 callable 时，返回 True 就释放 reservation；
                传 `NEVER_EXPIRES` 表示持续到 planner reset，不会自动结束。
            on_finish: reservation 释放时调用的无参回调。`until=NEVER_EXPIRES`
                时不允许传入，因为永久 reservation 不会自动 finish。

        Returns:
            `RequestHandle`，可用于查询 reservation 是否仍 pending，或组合其他
            生命周期条件。
        """

        if not reservations:
            return None
        if until is None:
            raise ValueError("reserve_actions() requires until=callable or until=NEVER_EXPIRES")
        if until is NEVER_EXPIRES and on_finish is not None:
            raise ValueError("reserve_actions(until=NEVER_EXPIRES) cannot use on_finish")
        request = _ReservationRequest(
            reason=reason or f"{self.current_char} action reservation",
            _source=self.current_char.index,
            until=until,
            on_finish=on_finish,
            reservations=reservations,
        )
        self._publish_request(request)
        return request.handle

    def request_switch(
        self,
        target: "BaseChar",
        reason: str = "",
        until: RequestDeadline | None = None,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """请求下一次普通调度优先切给目标角色。

        这是纯切人请求，不要求目标执行指定动作，也不会打断当前角色的动作链。
        strict route、entry reaction、环合反应仍拥有更高优先级；若它们先发生，
        此请求会保留到后续普通调度，直到切到目标或 `until()` 过期。

        Args:
            target: 希望切入的目标角色。为 None 时不会发布请求，并返回 None。
            reason: 日志和调试用理由；为空时会自动生成默认理由。
            until: 过期条件。为 None 时不会因时间/机制条件过期；返回 True 时
                switch request 以 EXPIRED 结束。
            on_finish: 请求首次出现 FULFILLED 或 EXPIRED 信号时调用一次。切到
                目标或目标已在场时是 FULFILLED；目标失效或 until 触发时是 EXPIRED。

        Returns:
            `RequestHandle`，可用于查询切人请求最终状态。
        """

        if target is None:
            return None
        request = _SwitchRequest(
            reason=reason or f"{self.current_char} requests switch to {target}",
            _source=self.current_char.index,
            until=until,
            on_finish=on_finish,
            target_index=target.index,
        )
        self._publish_request(request)
        return request.handle

    def request_role(
        self,
        role: Role,
        reason: str = "",
        until: RequestDeadline | None = None,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """请求下一次普通调度切给指定队伍定位的角色。

        这是纯切人请求, 不要求目标执行特定 action, 也不会打断当前角色的动作链。
        有多个存活角色声明该定位时, planner 会按普通切人评分从中选择目标;
        strict route、entry reaction 和环合反应仍拥有更高优先级。

        Args:
            role: 目标角色在 `RoleProfile.role` 中声明的队伍定位。
            reason: 日志和调试用理由; 为空时会自动生成默认理由。
            until: 过期条件。为 None 时不会因时间/机制条件过期; 返回 True 时
                request 以 EXPIRED 结束。
            on_finish: 请求首次出现 FULFILLED 或 EXPIRED 信号时调用一次。切到
                任一匹配定位的角色, 或该角色已在场时是 FULFILLED; 没有可切入的
                匹配角色或 until 触发时是 EXPIRED。

        Returns:
            `RequestHandle`, 可用于查询 role request 最终状态。
        """

        if role is None:
            return None
        request = _RoleRequest(
            reason=reason or f"{self.current_char} requests role {role}",
            _source=self.current_char.index,
            until=until,
            on_finish=on_finish,
            role=role,
        )
        self._publish_request(request)
        return request.handle

    def request_tags(
        self,
        tags: set[ActionTag],
        count: int = 1,
        reason: str = "",
        until: RequestDeadline | None = None,
        avoid_source: bool = True,
        return_to_source: bool = False,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """发布按动作标签寻找队友的通用协作请求。

        这是高级逃生口。普通协作优先使用 `request_route()` 明确指定
        “谁做什么槽位”；只有当需求确实是“任意队友完成某类通用动作”时
        才使用 tag request。

        Args:
            tags: 需要满足的动作标签集合。目标动作只要命中其中任一 tag 即可计数。
            count: 需要满足的次数/角色数；小于等于 0 时不会发布请求。
            reason: 日志和调试用理由；为空时会自动生成默认理由。
            until: 过期条件。为 None 时不会因时间/机制条件过期；返回 True 时
                request 以 EXPIRED 结束。
            avoid_source: 未完成前是否避免让发起者自己满足这个 tag request。
            return_to_source: request 成功完成后，是否提高发起者重新入场优先级。
            on_finish: 请求首次出现 FULFILLED 或 EXPIRED 信号时调用一次。

        Returns:
            `RequestHandle`，可用于查询 tag request 状态。
        """

        if not tags or count <= 0:
            return None
        request = _TagRequest(
            reason=reason or f"{self.current_char} tag request",
            _source=self.current_char.index,
            until=until,
            return_to_source=return_to_source,
            on_finish=on_finish,
            required_tags=set(tags),
            count=count,
            avoid_source=avoid_source,
        )
        self._publish_request(request)
        return request.handle

    def _publish_request(self, request: _Request | None) -> None:
        """在当前动作执行期间发布新的内部协作请求。"""

        if request is not None:
            self._published_requests.append(request)

    def _consume_published_requests(self) -> list[_Request]:
        """取出并清空本次动作发布的协作请求。"""

        requests = list(self._published_requests)
        self._published_requests.clear()
        return requests

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence

from typing_extensions import deprecated

from .binding import _original_method
from .compatibility import route_deadline, route_step
from .requests import (
    RequestDeadline,
    RequestLifetime,
    _Request,
    _ReservationRequest,
    _RoleRequest,
    _RouteRequest,
    _SwitchRequest,
    _TagRequest,
    _WindowRequest,
    request_counts_as_active,
    request_reserves_action,
)
from .types import (
    NEVER_EXPIRES,
    ActionIntent,
    ActionResult,
    ActionSlot,
    ActionTag,
    FollowupStep,
    Planner,
    RequestHandle,
    Role,
    _RouteStep,
)

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask

    from .core import CombatPlanner


@dataclass(frozen=True, slots=True)
class CombatContext:
    """通过 BaseChar.combat_context 查询队伍、发布路线、保留动作和管理窗口。"""

    _planner: CombatPlanner
    _generation: int
    current_char: "BaseChar"

    @property
    def task(self) -> "BaseCombatTask":
        return self._planner.task

    @property
    def chars(self) -> list["BaseChar"]:
        """当前 planner 管理的队伍角色列表。"""

        return self._planner._read(self, lambda: list(self._planner.state.chars))

    def has_active_request(self) -> bool:
        """查询已提交的协作请求, 包括等待当前 entry 结束后启用的路线。"""

        return self._planner._read(
            self,
            lambda: bool(
                self._planner.state.locked_route
                or self._planner.state.pending_routes
                or any(
                    request_counts_as_active(request)
                    for request in self._planner.state.active_requests
                )
            ),
        )

    def has_strict_route(self) -> bool:
        return self._planner._read(self, lambda: self._planner.state.locked_route is not None)

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

        def query():
            request = self._planner.state.locked_route
            step = request.current_step() if request else None
            if step is None:
                return False
            if step._method is not None:
                action = self._planner._resolve_route_method(char, step._method)
                if action is None:
                    action = self._planner._unregistered_route_action(char, step._method)
                return step.wants(char, action) and (
                    (slot is None or action.slot == slot)
                    and (not action_name or action.name == action_name)
                    and (not tags or bool(tags.intersection(action.tags)))
                )
            return step.wants(char, ActionResult(name=action_name, tags=set(tags or ()), slot=slot))

        return self._planner._read(self, query)

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

        def query():
            action = ActionResult(name=action_name, tags=set(tags or ()), slot=slot)
            if self.strict_route_wants_action(char, slot, action_name, tags):
                return True
            return not any(
                request_reserves_action(request, char, action)
                for request in self._planner.state.active_requests
            )

        return self._planner._read(self, query)

    @deprecated("Call the registered method; planner checks execution permission", category=None)
    def is_action_allowed(self, char: "BaseChar", action: ActionIntent) -> bool:
        """返回 planner 是否允许指定角色执行完整 action 声明。

        同时检查 action 自身的 `can_execute` 限制和 slot reservation。角色代码
        需要在 entry flow 外预查询动作时可以使用此方法; 普通执行仍应直接
        `yield action`，由 planner 在执行前统一检查。
        """

        from .compatibility import action_definition

        return self._planner._read(
            self, lambda: self._planner._action_allowed(char, action_definition(action, char))
        )

    def open_window(
        self,
        *,
        until: RequestDeadline,
        reason: str = "",
        on_finish: Callable[[], None] | None = None,
    ) -> CombatWindow:
        """创建独立协作窗口; 子路线完成或失败均不会提前结束窗口。"""

        request = _WindowRequest(
            reason=reason or f"{self.current_char} cooperation window",
            _source=self.current_char.index,
            until=until,
            on_finish=on_finish,
        )
        self._publish_request(request)
        return CombatWindow(self._planner, self._generation, self.current_char, request)

    def request_route(
        self,
        steps: (
            Callable[..., ActionResult | bool | None]
            | BaseChar
            | Planner.Route
            | FollowupStep
            | Sequence[
                Callable[..., ActionResult | bool | None] | BaseChar | Planner.Route | FollowupStep
            ]
        ),
        reason: str = "",
        until: RequestDeadline | None = None,
        return_to_source: bool = False,
        on_finish: Callable[[], None] | None = None,
        *,
        timeout: float | None = None,
    ) -> RequestHandle | None:
        """发布固定顺序协作路线。

        新路线在当前 entry 结束后启用, 不打断后续状态更新或合法动作。
        方法步骤按默认参数调用绑定方法; 已注册时沿用登记信息, 未注册也可执行。
        角色步骤执行目标的整轮 entry。
        必需动作不可用时以短暂普攻等待; 执行失败时保留当前步骤, 下次调度重试。
        全部步骤完成后继续当前角色的普通 entry。

        Args:
            steps: 队伍角色的绑定方法、角色、Planner.Route 步骤, 或按顺序排列的列表。
                action(char, slot=..., tags=...) 按注册顺序选择满足条件的可执行动作。
                optional(method) 在不可用或执行失败时跳过; reaction(A, B) 要求
                A 满资源后切入 B, 并等待对应的实际环合确认事件。
                旧 FollowupStep 已弃用, 仍可使用或与新步骤混合。
                列表为空时不会发布请求, 并返回 None。
            reason: 日志和调试用理由；为空时会自动使用当前角色生成默认理由。
            until: 过期条件。为 None 时不会因时间/机制条件过期；传 callable 时，
                每次 planner prune 都会调用，返回 True 则 route 以 EXPIRED 结束。
            return_to_source: route 成功完成后，是否提高发起者重新入场的优先级。
            on_finish: route 首次出现 FULFILLED、EXPIRED 或 FAILED 信号时调用一次。
                需要分别处理成功和过期时，保存返回的 handle 后使用
                `handle.on_fulfilled(...)` / `handle.on_expired(...)`。
            timeout: 整条路线的超时秒数, 不与 until 同时使用。

        Returns:
            `RequestHandle`，可用于查询 route 状态，或把 `handle.when.*`
            传给其他 request 的 `until=`。
        """

        if timeout is not None:
            if until is not None:
                raise ValueError("use either timeout or until, not both")
            if timeout < 0:
                raise ValueError("timeout must be nonnegative")
            until = self.task.clock.timer(timeout=timeout).expired
        if not isinstance(steps, Sequence):
            steps = [steps]
        if not steps:
            return None
        legacy = all(isinstance(step, FollowupStep) for step in steps)
        normalized = []
        for step in steps:
            optional = False
            if isinstance(step, Planner.Route):
                if step._slot is not None or step._tags:
                    target = step._target
                    if target is None or not any(target is char for char in self.chars):
                        raise ValueError("route action target must be a current character")
                    normalized.append(
                        _RouteStep(
                            reason=f"{target} action selection",
                            slot=step._slot,
                            required_tags=set(step._tags),
                            optional=step._optional,
                            target_indices={target.index},
                        )
                    )
                    continue
                if step._method is None:
                    source, target = step._source, step._target
                    if not all(
                        any(char is member for member in self.chars) for char in (source, target)
                    ):
                        raise ValueError("reaction source and target must be current characters")
                    normalized.append(
                        _RouteStep(
                            reason=f"{source} -> {target} reaction",
                            slot=ActionSlot.ENTRY_REACTION,
                            target_indices={target.index},
                            requires_entry_reaction=True,
                            optional=step._optional,
                            _reaction_source=source.index,
                        )
                    )
                    continue
                optional = step._optional
                step = step._method
            if isinstance(step, FollowupStep):
                normalized.append(route_step(step))
            elif any(step is char for char in self.chars):
                normalized.append(
                    _RouteStep(
                        reason=f"{step} normal turn",
                        target_indices={step.index},
                        switch_step=True,
                        wait_for_turn=True,
                    )
                )
            elif callable(step):
                method = _original_method(step)
                owner = getattr(method, "__self__", None)
                if not any(owner is char for char in self.chars):
                    raise ValueError("route methods must belong to a current teammate")
                normalized.append(
                    _RouteStep(
                        reason=f"{owner}.{method.__name__}",
                        target_indices={owner.index},
                        _method=method,
                        optional=optional,
                    )
                )
            else:
                raise TypeError("route steps must be character methods or current characters")
        request = _RouteRequest(
            reason=reason or f"{self.current_char} route request",
            _source=self.current_char.index,
            until=until,
            return_to_source=return_to_source,
            on_finish=on_finish,
            steps=normalized,
        )
        self._publish_request(request)
        if legacy:
            route_deadline(self, request, until)
        return request.handle

    def reserve_actions(
        self,
        reservations: Sequence[Planner.Reserve],
        reason: str = "",
        *,
        until: RequestLifetime,
        on_finish: Callable[[], None] | None = None,
    ) -> RequestHandle | None:
        """发布纯动作保留请求。

        reservation 只负责阻止指定队友动作被普通流程提前消耗。它不推进 route，
        不代表请求完成，也不会主动要求目标角色执行动作。

        Args:
            reservations: 用 Planner.Reserve.action(...) 声明的保留集合; 为空时不发布请求。
            reason: 日志和调试用理由；为空时会自动使用当前角色生成默认理由。
            until: 必填生命周期。传 callable 时，返回 True 就释放 reservation；
                传 `NEVER_EXPIRES` 表示持续到 planner reset，不会自动结束。
            on_finish: reservation 到期时调用的无参回调; 永久保留不接受此参数。

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
            reservations=list(reservations),
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
            self._planner._submit_request(self, request)


@dataclass(slots=True)
class CombatWindow:
    """A cycle-owned lifetime shared by its routes and reservations."""

    _planner: CombatPlanner
    _generation: int
    _source: BaseChar
    _request: _WindowRequest

    @property
    def handle(self) -> RequestHandle:
        return self._request.handle

    def request_route(
        self,
        steps: Callable[..., ActionResult | bool | None]
        | BaseChar
        | Planner.Route
        | Sequence[Callable[..., ActionResult | bool | None] | BaseChar | Planner.Route],
        reason: str = "",
        on_finish: Callable[[], None] | None = None,
        *,
        return_to_source: bool = False,
    ) -> RequestHandle | None:
        if isinstance(steps, Sequence) and not steps:
            return None
        return self._planner._window_call(
            self,
            lambda context: context.request_route(
                steps, reason=reason, on_finish=on_finish, return_to_source=return_to_source
            ),
        )

    def reserve_actions(
        self,
        reservations: Sequence[Planner.Reserve],
        reason: str = "",
    ) -> RequestHandle | None:
        if not reservations:
            return None
        return self._planner._window_call(
            self,
            lambda context: context.reserve_actions(
                reservations, reason=reason, until=NEVER_EXPIRES
            ),
        )

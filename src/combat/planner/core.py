from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from ok import Logger

from src.utils.log_gate import LogGate

from .context import CombatContext
from .requests import (
    _Request,
    _RouteRequest,
    request_blocks_entry_flow,
    request_current_step,
    request_fulfilled,
    request_is_switch,
    request_switch_targets,
    request_wants_action,
)
from .state import CombatState, _PlanSnapshot
from .types import (
    ACTION_TAG_SCORES,
    FIELD_CLAIM_SCORES,
    NEVER_EXPIRES,
    ActionExecutor,
    ActionIntent,
    ActionPredicate,
    ActionReservation,
    ActionResult,
    ActionSlot,
    ActionTag,
    CombatPlan,
    ExpectedEntry,
    FieldClaim,
    FieldClaimLevel,
    FieldPreference,
    FollowupStep,
    Planner,
    RequestHandle,
    RequestStatus,
    Role,
    RoleProfile,
    SwitchDecision,
    SwitchInGuard,
)

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask


logger = Logger.get_logger("planner")


__all__ = [
    "NEVER_EXPIRES",
    "ActionIntent",
    "ActionReservation",
    "ActionResult",
    "ActionSlot",
    "ActionTag",
    "ActionExecutor",
    "ActionPredicate",
    "CombatContext",
    "CombatPlan",
    "CombatPlanner",
    "ExpectedEntry",
    "FieldClaim",
    "FieldClaimLevel",
    "FieldPreference",
    "FollowupStep",
    "Planner",
    "RequestHandle",
    "RequestStatus",
    "Role",
    "RoleProfile",
    "SwitchDecision",
    "SwitchInGuard",
]


@dataclass(slots=True)
class _ScoreBreakdown:
    """普通切人评分的可读拆解；只用于日志和调试，不参与调度规则。"""

    parts: list[tuple[str, int]] = field(default_factory=list)

    def add(self, label: str, value: int) -> int:
        if value:
            self.parts.append((label, value))
        return value

    @property
    def total(self) -> int:
        return sum(value for _, value in self.parts)

    def format(self) -> str:
        if not self.parts:
            return "score=0"
        parts = ", ".join(f"{label}={value:+d}" for label, value in self.parts)
        return f"{parts} => {self.total}"


@dataclass(slots=True)
class _EntrySession:
    """一次在场角色入场内共享的 action flow 状态。"""

    char: "BaseChar"
    context: CombatContext
    entry_flow: Any | None = None
    pending_entry_action: ActionIntent | None = None
    performed_results: dict[str, ActionResult] = field(default_factory=dict)
    last_result: ActionResult | None = None
    successful_action: bool = False
    yielded_before_action: bool = False
    steps: int = 0


class CombatPlanner:
    """队伍协作规划器。

    这是战斗系统的大脑：角色通过 `ActionIntent` 声明自己能尝试的动作，通过
    `CombatContext` 声明协作需求；planner 负责切人、执行动作、推进请求和
    生成内建站场动作。

    普通评分不会累加同一角色的所有 actions。planner 会先过滤出
    `is_priority_ready(context)` 为 True 的动作，然后挑分数最高的那个 action
    代表该角色参与切人竞争。action 分数来自 `ActionIntent.tags` 对应的
    `ACTION_TAG_SCORES`，再叠加协作请求和角色 `RoleProfile` 的评分。

    角色切入后，普通路径由 `CombatPlan.entry` 控制；未声明 entry 时按 actions
    顺序执行，最多执行 `MAX_ACTIONS_PER_ENTRY` 次。同名 action 在同一次入场中
    只会执行一次。

    `SKILL_ACTION` 和 `ULTIMATE_ACTION` 已经包含 E/Q 的默认输出评分；`DAMAGE`
    只用于普攻站场、旧出招表或非 E/Q 的额外伤害动作。

    切人统一由 `decide_switch()` 负责。perform 后的普通评分固定排除当前角色；
    角色想抢回场应使用 `FieldClaim`，角色想延迟被切入应使用 `SwitchInGuard`。

    关键行为:
        - 切人评分与进场执行顺序分离。评分选出的最佳 action 只用于判断目标角色
          是否值得切入；普通切入后仍按角色声明顺序尝试 action。
        - strict route 和普通 claim 可以设置 expected entry 并优先尝试该动作。
        - `priority_ready` 只用于评分；`can_execute` 是硬限制。
    """

    MAX_ACTIONS_PER_ENTRY = 20
    LOG_THROTTLE_INTERVAL = 0.5

    def __init__(self, task: "BaseCombatTask") -> None:
        """创建 planner，并绑定所属 `BaseCombatTask`。"""

        self.task = task
        self.state = CombatState()
        self._log_gate = LogGate(logger)

    def reset(self, chars: Iterable["BaseChar"]) -> None:
        """重置 planner 管理的队伍角色和运行状态。

        会清空所有请求、route、pending expected entry，并重新执行每个角色的
        `combat_policies(context)` 发布长期策略。
        """

        self.state.reset(chars)
        self._apply_combat_policies()

    def _apply_combat_policies(self) -> None:
        """让角色发布随队伍生命周期生效的长期 planner 策略。"""

        for char in self.state.chars:
            combat_policies = getattr(char, "combat_policies", None)
            if combat_policies is None:
                continue
            context = CombatContext(task=self.task, _state=self.state, current_char=char)
            combat_policies(context)
            requests = context._consume_published_requests()
            if not requests:
                continue
            self._ensure_followup_sources(char, requests)
            self.state.add_requests(requests)

    def context_for(
        self,
        current_char: "BaseChar",
        plan_cache: dict[int, _PlanSnapshot] | None = None,
    ) -> CombatContext:
        """为当前角色创建 `CombatContext`。

        创建前会先 prune 过期请求。返回的 context 用于一次动作执行或一次查询。
        """

        self.state.prune()
        return CombatContext(
            task=self.task,
            _state=self.state,
            current_char=current_char,
            _plan_cache=plan_cache,
        )

    def _log_info_throttled(self, key, message: str, interval: float | None = None):
        interval = self.LOG_THROTTLE_INTERVAL if interval is None else interval
        self._log_gate.info(message, interval=interval, key=key)

    def switch_in_guard(
        self,
        from_char: "BaseChar",
        target_char: "BaseChar",
        has_intro: bool,
    ) -> SwitchInGuard:
        """查询目标角色是否允许现在被切入。

        这是入场延迟的唯一 planner 入口。角色默认允许立即切入；特殊角色可覆盖
        `BaseChar.switch_in_guard()` 返回延迟条件。
        """

        plan_cache: dict[int, _PlanSnapshot] = {}
        context = self.context_for(target_char, plan_cache)
        guard = target_char.switch_in_guard(context, from_char, has_intro)
        return guard or SwitchInGuard.allow()

    def has_strict_route(self, current_char: "BaseChar") -> bool:
        """返回当前是否存在正在锁定执行的 strict route。"""

        return self.context_for(current_char).has_strict_route()

    def decide_combat_start_char(self, current_char: "BaseChar | None") -> SwitchDecision:
        """决定战斗刚开始时是否需要首切到指定角色。

        首切只读取 `RoleProfile.combat_start_priority`，不参与普通动作评分，也不触发
        strict route / 环合反应等战斗中调度。
        """

        candidates = []
        for char in self.state.chars:
            if not self._can_switch_to(char):
                continue
            priority = char.describe_role().combat_start_priority
            if priority > 0:
                candidates.append((priority, char))

        if not candidates:
            return SwitchDecision(
                target=current_char,
                reason="no combat start target",
                priority=-999999,
                has_intro=False,
                expected_entry=None,
            )

        priority, target = min(
            candidates,
            key=lambda item: (
                -item[0],
                getattr(item[1], "last_switch_time", self.task.clock.UNSET),
                getattr(item[1], "index", 0),
            ),
        )
        return SwitchDecision(
            target=target,
            reason="combat start priority",
            priority=priority,
            has_intro=False,
            expected_entry=None,
        )

    def record_entry_reaction(self, source_char: "BaseChar", target_char: "BaseChar") -> None:
        """通知 planner 已发生一次入场/环合反应。"""

        self.state.record_entry_reaction(source_char, target_char)

    def record_switch(self, target_char: "BaseChar") -> None:
        """通知 planner 已实际切到某角色，用于消费纯切人请求。"""

        self.state.record_switch(target_char)

    def prepare_switch(self, decision: SwitchDecision) -> None:
        """准备执行切人决策，并清理目标角色残留的入场动作期望。"""

        self.state.set_pending_entry_expectation(decision.target, None)

    def complete_switch(
        self,
        decision: SwitchDecision,
        previous_char: "BaseChar | None" = None,
        entry_reaction: bool = False,
    ) -> None:
        """记录已确认的切人，并应用最终决策的入场动作期望。"""

        target_char = decision.target
        if previous_char is not None and previous_char != target_char:
            self.state.set_pending_entry_expectation(previous_char, None)
        self.state.set_pending_entry_expectation(target_char, decision.expected_entry)
        if entry_reaction and previous_char is not None:
            self.record_entry_reaction(previous_char, target_char)
        self.record_switch(target_char)

    def perform_current_char(self, current_char: "BaseChar") -> ActionResult | None:
        """规划并执行当前在场角色的动作。

        单次入场最多执行 `MAX_ACTIONS_PER_ENTRY` 个动作；如果所有动作都失败或没有
        动作，会尝试 planner 内建的 field time fallback。

        如果执行函数返回 bool/None，`ActionResult.tags` 会继承 action tags；
        因此一般不需要手写 result tags。`ActionResult.tags` 不再影响 entry flow 控制。

        执行优先级:
            1. pending expected entry，例如 strict route 切入后的强制首动。
            2. 当前 strict route 的目标 action。
            3. active tag request 可完成的 action。
            4. 角色 `combat_plan().entry` 产出的普通入场动作；未定义 entry 时按
               `combat_plan().actions` 声明顺序执行。

        返回:
            最后一次执行的 `ActionResult`，或没有动作时返回 None。
        """

        session = self._entry_session_for(current_char)
        turn_route = None
        turn_step = None

        while session.steps < self.MAX_ACTIONS_PER_ENTRY:
            if session.steps == 0 and self._should_return_to_requester_before_action(
                current_char, session.context
            ):
                self._log_info_throttled(
                    ("return_before_action", current_char.index),
                    f"planner return to requester before action {current_char}",
                )
                session.yielded_before_action = True
                break

            action, scheduled = self._next_session_action(session)
            if not scheduled and self._route_waits_for_turn(current_char):
                # Capture the step after route advancement, before running normal actions.
                turn_route = self.state.locked_route
                turn_step = turn_route.current_step()
            if action is None:
                if session.steps == 0:
                    if self._should_return_to_requester_before_action(
                        current_char,
                        session.context,
                    ):
                        self._log_info_throttled(
                            ("return_before_action", current_char.index),
                            f"planner return to requester before action {current_char}",
                        )
                        session.yielded_before_action = True
                        break
                    self._log_info_throttled(
                        ("action_none", current_char.index),
                        f"planner action none {current_char}",
                    )
                break

            result, executed = self._execute_entry_action(
                current_char,
                action,
                session.context,
            )
            session.steps += 1
            self._record_session_result(session, action, result, executed)

            if executed and self._skip_failed_optional_route_step(current_char, action, result):
                continue
            if not self._should_continue_entry(current_char, result):
                break

            if not scheduled and session.entry_flow is not None:
                session.pending_entry_action = self._advance_entry_flow(
                    session.entry_flow,
                    session.context,
                    result,
                )
                if session.pending_entry_action is None:
                    break
                if self._ordinary_entry_blocked(current_char, session.context):
                    break

        if (
            not session.successful_action
            and not session.yielded_before_action
            and self._can_use_field_time_fallback(current_char)
        ):
            context = self.context_for(current_char)
            fallback_result = self._perform_field_time_fallback(current_char, context)
            if fallback_result is not None:
                session.last_result = fallback_result

        if (
            turn_route is not None
            and self.state.locked_route is turn_route
            and turn_route.current_step() is turn_step
            and not session.yielded_before_action
            and self._route_waits_for_turn(current_char)
            and not self.state.expire_locked_route()
        ):
            turn_route.complete_turn(current_char)
            if turn_route.fulfilled():
                self.state.fulfill_locked_route()
        return session.last_result

    def _ensure_followup_sources(
        self, current_char: "BaseChar", requests: Iterable[_Request]
    ) -> None:
        for request in requests:
            if request._source < 0:
                request._source = current_char.index
            if not request.reason:
                request.reason = f"{current_char} planner request"

    def _entry_session_for(self, current_char: "BaseChar") -> _EntrySession:
        return _EntrySession(
            char=current_char,
            context=self.context_for(current_char, {}),
        )

    def _next_session_action(
        self,
        session: _EntrySession,
    ) -> tuple[ActionIntent | None, bool]:
        skipped_route_actions: list[ActionIntent] = []
        scheduled_action = self._scheduled_action_for(
            session.char,
            session.context,
            set(session.performed_results),
            skipped_route_actions,
        )
        for action in skipped_route_actions:
            session.performed_results.setdefault(
                action.identity_key(),
                ActionResult(
                    name=action.name,
                    success=False,
                    tags=set(action.tags),
                    slot=action.slot,
                    reason="optional route step skipped by planner",
                ),
            )
        if scheduled_action is not None:
            return scheduled_action, True

        action = self._next_entry_flow_action(session)
        if action is None:
            return None, False
        session.pending_entry_action = None
        return action, False

    def _next_entry_flow_action(self, session: _EntrySession) -> ActionIntent | None:
        if session.entry_flow is None:
            session.entry_flow = self._entry_flow_for(session.char, session.context)
            session.pending_entry_action = self._advance_entry_flow(
                session.entry_flow,
                session.context,
            )

        while session.pending_entry_action is not None:
            performed_result = session.performed_results.get(
                session.pending_entry_action.identity_key()
            )
            if performed_result is None:
                return session.pending_entry_action
            if session.steps >= self.MAX_ACTIONS_PER_ENTRY:
                return None
            session.steps += 1
            session.pending_entry_action = self._advance_entry_flow(
                session.entry_flow,
                session.context,
                performed_result,
            )
            if session.steps >= self.MAX_ACTIONS_PER_ENTRY:
                return None
            if session.pending_entry_action is not None and self._ordinary_entry_blocked(
                session.char,
                session.context,
            ):
                return None

        return None

    def _record_session_result(
        self,
        session: _EntrySession,
        action: ActionIntent,
        result: ActionResult,
        executed: bool,
    ) -> None:
        if executed:
            session.performed_results[action.identity_key()] = result
        session.last_result = result
        session.successful_action = session.successful_action or result.success

    def _entry_expected_action(
        self, current_char: "BaseChar", context: CombatContext
    ) -> ActionIntent | None:
        expected_entry = self.state.pop_pending_entry_expectation(current_char)
        if expected_entry is None:
            return None
        actions = self._actions_for(current_char, context)
        for action in actions:
            if expected_entry.matches(action) and self._action_allowed(
                current_char, action, context
            ):
                logger.info(f"planner entry expected action {current_char} -> {action.name}")
                return action
        logger.info(f"planner entry expected action unavailable {current_char} -> {expected_entry}")
        return None

    def _scheduled_action_for(
        self,
        char: "BaseChar",
        context: CombatContext,
        excluded_action_names: set[str],
        skipped_route_actions: list[ActionIntent] | None = None,
    ) -> ActionIntent | None:
        action = self._entry_expected_action(char, context)
        if action is not None and action.identity_key() not in excluded_action_names:
            return action

        skipped_actions = self._advance_ready_route_steps(context)
        if skipped_route_actions is not None:
            skipped_route_actions.extend(skipped_actions)
        if self._should_return_to_requester_before_action(char, context):
            return None
        actions = self._actions_for(char, context)
        route_action = self._strict_route_action(char, actions, context)
        if route_action is not None and route_action.identity_key() not in excluded_action_names:
            return route_action
        route_wait = self._strict_route_wait_action(char, context)
        if route_wait is not None and route_wait.identity_key() not in excluded_action_names:
            return route_wait
        if not context._state.active_requests:
            return None
        allowed_actions = [
            action
            for action in actions
            if action.identity_key() not in excluded_action_names
            and self._action_allowed(char, action, context)
        ]
        if self._strict_route_request(context) is not None or not allowed_actions:
            return None
        return self._active_request_action(char, allowed_actions, context)

    def _entry_flow_for(self, char: "BaseChar", context: CombatContext):
        plan = self._plan_snapshot_for(char, context)
        if plan.entry is not None:
            return plan.entry()

        def default_entry():
            for action in plan.actions:
                yield action

        return default_entry()

    def _advance_entry_flow(
        self,
        entry_flow,
        context: CombatContext,
        result: ActionResult | None = None,
    ) -> ActionIntent | None:
        try:
            action = next(entry_flow) if result is None else entry_flow.send(result)
        except StopIteration:
            action = None
        published_requests = context._consume_published_requests()
        self._ensure_followup_sources(context.current_char, published_requests)
        self.state.add_requests(published_requests)
        return action

    def _execute_entry_action(
        self,
        current_char: "BaseChar",
        action: ActionIntent,
        context: CombatContext,
    ) -> tuple[ActionResult, bool]:
        if not self._action_allowed(current_char, action, context):
            return (
                ActionResult(
                    name=action.name,
                    success=False,
                    tags=set(action.tags),
                    slot=action.slot,
                    reason="action blocked by planner",
                ),
                False,
            )

        action_name = action.display_name()
        logger.info(
            f"planner action {current_char} -> {action_name}, "
            f"tags {sorted(str(tag) for tag in action.tags)}, reason {action.reason}"
        )
        result = action.run(context)
        published_requests = context._consume_published_requests()
        self._ensure_followup_sources(current_char, published_requests)
        self.state.record_action(current_char, result)
        self.state.add_requests(published_requests)
        return result, True

    def decide_switch(
        self,
        current_char: "BaseChar",
        free_intro: bool = False,
        require_intro: bool = False,
    ) -> SwitchDecision:
        """根据当前状态决定是否切人以及切给谁。

        优先级依次为已锁定的 strict route, strict claim, 入场/环合请求,
        游戏环合反应, 普通动作评分。strict claim 只指定切入目标。
        普通评分只决定“谁值得切出来”，不改写目标角色自己的动作声明顺序。

        普通动作评分时，每个角色只用自己的最佳 action 参与比较；多个 action 的
        tag 分数不会相加。若某个高分 action 的 `priority_ready` 过宽，会让该角色
        过度吸引切人。

        当前角色本轮 perform 已结束，普通评分固定排除 current。strict route、
        entry reaction、环合反应等硬调度不受此限制；若没有其他有效候选，planner
        会保留 current。

        参数:
            current_char: 当前在场角色。
            free_intro: 强制认为当前有入场/环合资源。
            require_intro: 只接受能触发 intro 的目标；通常用于切人过程中重算。

        返回:
            `SwitchDecision`。strict route 或普通 claim 可设置 `expected_entry`,
            使匹配的动作在切入后优先尝试。
        """

        plan_cache: dict[int, _PlanSnapshot] = {}
        context = self.context_for(current_char, plan_cache)
        has_intro = free_intro or current_char.is_cycle_full()
        if require_intro and not has_intro:
            return SwitchDecision(current_char, "intro required but not ready", -999999, has_intro)

        route_decision = self._strict_route_decision(current_char, context, has_intro)
        if route_decision is not None:
            self._log_switch_decision(current_char, route_decision)
            return route_decision

        claim_decision = self._strict_claim_decision(current_char, context, has_intro)
        if claim_decision is not None:
            self._log_switch_decision(current_char, claim_decision)
            return claim_decision

        entry_request_decision = self._entry_reaction_request_decision(
            current_char, context, has_intro
        )
        if entry_request_decision is not None:
            self._log_switch_decision(current_char, entry_request_decision)
            return entry_request_decision

        reaction_decision = self._element_reaction_decision(current_char, has_intro)
        if reaction_decision is not None:
            self._log_switch_decision(current_char, reaction_decision)
            return reaction_decision

        switch_request_decision = self._switch_request_decision(current_char, context, has_intro)
        if switch_request_decision is not None:
            self._log_switch_decision(current_char, switch_request_decision)
            return switch_request_decision

        best_decision = SwitchDecision(
            target=current_char,
            reason="no switch target",
            priority=-999999,
            has_intro=has_intro,
            expected_entry=None,
        )

        for char in self.state.chars:
            if not self._can_switch_to(char):
                continue
            if char == current_char:
                continue
            score, reason, expected, breakdown = self._score_char(
                char,
                context,
                current_char=(char == current_char),
            )
            if score <= -10000:
                continue
            if char != current_char and self._switch_on_cooldown(char, has_intro):
                score -= 1000
                breakdown.add("switch_cooldown", -1000)
                reason = "switch cooldown"
            if score > best_decision.priority or (
                score == best_decision.priority
                and char.last_perform < best_decision.target.last_perform
            ):
                best_decision = SwitchDecision(
                    char,
                    reason,
                    score,
                    has_intro,
                    expected,
                    breakdown.format(),
                )

        self._log_switch_decision(current_char, best_decision)
        return best_decision

    def _active_request_action(
        self,
        char: "BaseChar",
        actions: list[ActionIntent],
        context: CombatContext,
    ) -> ActionIntent | None:
        """按角色声明顺序选择能完成 active request 的动作。"""

        for action in actions:
            if any(
                request_wants_action(request, char, action)
                for request in context._state.active_requests
            ):
                return action
        return None

    def _best_scoring_action_for(
        self,
        char: "BaseChar",
        context: CombatContext,
    ) -> ActionIntent | None:
        """选择用于切人评分的最佳动作。

        此路径会使用 `priority_ready`，避免不可用 Q/E 过度吸引 planner 切人。
        选出的 action 只代表角色参与切人评分，不代表进场后的首个动作。
        """

        actions = [
            action
            for action in self._actions_for(char, context)
            if self._action_priority_ready(char, action, context)
        ]
        if not actions:
            return None
        return max(actions, key=lambda action: self._score_action(char, action, context))

    def _should_return_to_requester_before_action(
        self, current_char: "BaseChar", context: CombatContext
    ) -> bool:
        for request in context._state.active_requests:
            if (
                request.return_to_source
                and request_fulfilled(request)
                and current_char.index != request._source
            ):
                return True
        return False

    def _skip_failed_optional_route_step(
        self,
        current_char: "BaseChar",
        action: ActionIntent,
        result: ActionResult,
    ) -> bool:
        if result.success:
            return False
        request = self.state.locked_route
        if request is None:
            return False
        step = request.current_step()
        if step is None or not step.optional:
            return False
        if not step.wants(current_char, action):
            return False

        logger.info(f"strict route skips failed optional step: {request.reason} / {step.reason}")
        if not request.skip_current_step():
            return False
        if request.fulfilled():
            self.state.fulfill_locked_route()
            return False
        return True

    def _should_continue_entry(self, current_char: "BaseChar", result: ActionResult) -> bool:
        if not result.success:
            return self._can_try_next_action_after_failure(current_char)
        if self.state.locked_route is not None:
            return self._locked_route_can_continue_on_current(current_char)
        if any(request_blocks_entry_flow(request) for request in self.state.active_requests):
            return False
        return True

    def _can_try_next_action_after_failure(self, current_char: "BaseChar") -> bool:
        if self.state.locked_route is not None:
            return self._route_waits_for_turn(current_char)
        if any(request_blocks_entry_flow(request) for request in self.state.active_requests):
            return False
        return True

    def _ordinary_entry_blocked(self, current_char: "BaseChar", context: CombatContext) -> bool:
        if self.state.locked_route is not None:
            return not self._locked_route_can_continue_on_current(current_char)
        if self._should_return_to_requester_before_action(current_char, context):
            return True
        return any(request_blocks_entry_flow(request) for request in self.state.active_requests)

    def _can_use_field_time_fallback(self, current_char: "BaseChar") -> bool:
        if self.state.locked_route is not None:
            return self._route_waits_for_turn(current_char)
        return not any(request_blocks_entry_flow(request) for request in self.state.active_requests)

    def _locked_route_can_continue_on_current(self, current_char: "BaseChar") -> bool:
        request = self.state.locked_route
        if request is None:
            return False
        step = request.current_step()
        if step is None or step.requires_entry_reaction:
            return False
        return step.matches_char(current_char)

    def _route_waits_for_turn(self, char: "BaseChar") -> bool:
        request = self.state.locked_route
        step = request.current_step() if request is not None else None
        return bool(
            step is not None and step.switch_step and step.wait_for_turn and step.matches_char(char)
        )

    def _element_reaction_decision(
        self, current_char: "BaseChar", has_intro: bool
    ) -> SwitchDecision | None:
        if not has_intro:
            return None
        reaction_target = self.task.find_element_reaction_target(current_char)
        if not self._can_switch_to(reaction_target) or reaction_target == current_char:
            return None
        return SwitchDecision(
            reaction_target,
            "element reaction",
            999500,
            has_intro,
            None,
        )

    def _entry_reaction_request_decision(
        self, current_char: "BaseChar", context: CombatContext, has_intro: bool
    ) -> SwitchDecision | None:
        if not has_intro:
            return None
        for request in context._state.active_requests:
            step = request_current_step(request)
            if step is None or not step.requires_entry_reaction:
                continue
            target = self._strict_route_target(context, step)
            if not self._can_switch_to(target) or target == current_char:
                continue
            return SwitchDecision(
                target=target,
                reason=f"fulfill entry reaction request: {request.reason} / {step.reason}",
                priority=999000,
                has_intro=has_intro,
                expected_entry=None,
            )
        return None

    def _switch_request_decision(
        self, current_char: "BaseChar", context: CombatContext, has_intro: bool
    ) -> SwitchDecision | None:
        active_requests = []
        decision = None
        for request in context._state.active_requests:
            targets = request_switch_targets(request, context.chars)
            if not targets:
                if request_is_switch(request):
                    request.finish(RequestStatus.EXPIRED)
                    request.close()
                    logger.info(f"switch request target missing: {request.reason}")
                    continue
                active_requests.append(request)
                continue
            targets = [target for target in targets if self._can_switch_to(target)]
            if not targets:
                if request_is_switch(request):
                    request.finish(RequestStatus.EXPIRED)
                    request.close()
                    logger.info(f"switch request target dead: {request.reason}")
                    continue
                active_requests.append(request)
                continue
            if current_char in targets:
                request.finish(RequestStatus.FULFILLED)
                request.close()
                logger.info(f"switch request already current: {request.reason}")
                continue
            target = targets[0]
            if len(targets) > 1:
                target = min(
                    targets,
                    key=lambda char: (
                        -self._score_char(char, context, current_char=False)[0],
                        char.last_perform,
                        char.index,
                    ),
                )
            if decision is None:
                decision = SwitchDecision(
                    target=target,
                    reason=f"switch request: {request.reason}",
                    priority=998000,
                    has_intro=has_intro,
                    expected_entry=None,
                )
            active_requests.append(request)
        context._state.active_requests = active_requests
        return decision

    def _plan_snapshot_for(self, char: "BaseChar", context: CombatContext) -> _PlanSnapshot:
        if context._plan_cache is not None and char.index in context._plan_cache:
            return context._plan_cache[char.index]

        plan = char.combat_plan(context)
        actions = [action for action in plan.actions if action is not None]
        claims = []
        for claim in plan.claims:
            if claim is None:
                continue
            claim.ensure_source(char)
            claims.append(claim)
        followups = context._consume_published_requests()
        if followups:
            logger.warning(
                f"{char}.combat_plan() published planner requests; ignored. "
                "Publish long-lived requests in combat_policies(), or publish action "
                "followups from ActionIntent.execute()."
            )
        snapshot = _PlanSnapshot(actions=actions, claims=claims, entry=plan.entry)
        if context._plan_cache is not None:
            context._plan_cache[char.index] = snapshot
        return snapshot

    def _actions_for(self, char: "BaseChar", context: CombatContext) -> list[ActionIntent]:
        return list(self._plan_snapshot_for(char, context).actions)

    def _action_allowed(
        self, char: "BaseChar", action: ActionIntent, context: CombatContext
    ) -> bool:
        """统一判断 planner 是否允许某角色执行某动作。

        公开的 `CombatContext.is_action_allowed()` 也是同一条判断路径，避免角色
        预查询结果和真正执行规则不一致。
        """

        return context.is_action_allowed(char, action)

    def _action_priority_ready(
        self, char: "BaseChar", action: ActionIntent, context: CombatContext
    ) -> bool:
        """统一判断动作是否可用于切人评分。"""

        if not self._action_allowed(char, action, context):
            return False
        if action.priority_ready is None:
            return True
        return action.priority_ready(context)

    def _field_time_action(self, char: "BaseChar", context: CombatContext) -> ActionIntent | None:
        profile = char.describe_role()
        if profile.max_field_time <= 0:
            return None
        return ActionIntent(
            name="planner_field_time",
            tags={ActionTag.FIELD_TIME, ActionTag.DAMAGE},
            slot=ActionSlot.FIELD_TIME,
            execute=lambda _: self._execute_field_time(char, profile.max_field_time),
            reason=f"{profile.field_preference} field time fallback",
        )

    def _execute_field_time(self, char: "BaseChar", max_field_time: float) -> ActionResult:
        duration = max_field_time - char.time_elapsed_accounting_for_freeze(char.last_perform)
        if char.has_intro:
            duration += char.INTRO_MOTION_FREEZE_DURATION
        if duration > 0:
            char.continues_normal_attack(duration)
        return ActionResult(
            name="planner_field_time",
            success=duration > 0,
            tags={ActionTag.FIELD_TIME, ActionTag.DAMAGE},
            slot=ActionSlot.FIELD_TIME,
        )

    def _perform_field_time_fallback(
        self, char: "BaseChar", context: CombatContext
    ) -> ActionResult | None:
        action = self._field_time_action(char, context)
        if action is None:
            return None
        logger.info(
            f"planner fallback {char} -> {action.name}, "
            f"tags {sorted(str(tag) for tag in action.tags)}, reason {action.reason}"
        )
        result = action.run(context)
        self.state.record_action(char, result)
        self.state.add_requests(context._consume_published_requests())
        return result

    def _strict_route_request(self, context: CombatContext) -> _RouteRequest | None:
        request = context._state.locked_route
        if request is not None and request.current_step() is not None:
            return request
        return None

    def _advance_ready_route_steps(
        self,
        context: CombatContext,
    ) -> list[ActionIntent]:
        """完成已满足的到场步骤, 并跳过不可用的可选动作步骤。"""

        skipped_actions: list[ActionIntent] = []
        request = self._strict_route_request(context)
        while request is not None:
            if self._can_switch_to(context.current_char):
                context._state.complete_route_arrival_steps(context.current_char)
                request = self._strict_route_request(context)
                if request is None:
                    return skipped_actions
            step = request.current_step()
            if step is None or not step.optional:
                return skipped_actions
            if step.requires_entry_reaction:
                if self._route_step_blocked_by_dead_target(context, step):
                    logger.info(
                        f"strict route skips optional dead target step: "
                        f"{request.reason} / {step.reason}"
                    )
                    if request.skip_current_step():
                        if request.fulfilled():
                            context._state.fulfill_locked_route()
                            return skipped_actions
                        continue
                return skipped_actions
            target = self._strict_route_target(context, step)
            if target is None:
                if request.skip_current_step():
                    if request.fulfilled():
                        context._state.fulfill_locked_route()
                        return skipped_actions
                    continue
                return skipped_actions
            if target != context.current_char:
                return skipped_actions
            actions = self._actions_for(target, context)
            if any(
                step.wants(target, action) and self._action_priority_ready(target, action, context)
                for action in actions
            ):
                return skipped_actions
            logger.info(f"strict route skips optional step: {request.reason} / {step.reason}")
            skipped_actions.extend(action for action in actions if step.wants(target, action))
            if not request.skip_current_step():
                return skipped_actions
            if request.fulfilled():
                context._state.fulfill_locked_route()
                return skipped_actions
        return skipped_actions

    def _strict_route_action(
        self, char: "BaseChar", actions: list[ActionIntent], context: CombatContext
    ) -> ActionIntent | None:
        request = self._strict_route_request(context)
        if request is None:
            return None
        step = request.current_step()
        if step is None:
            return None
        if step.requires_entry_reaction:
            return None
        for action in actions:
            if not step.wants(char, action):
                continue
            if step.optional and not self._action_priority_ready(char, action, context):
                continue
            if self._action_allowed(char, action, context):
                return action
        return None

    def _strict_route_wait_action(
        self, char: "BaseChar", context: CombatContext
    ) -> ActionIntent | None:
        request = self._strict_route_request(context)
        if request is None:
            return None
        step = request.current_step()
        if (
            step is None
            or step.switch_step
            or step.requires_entry_reaction
            or not step.matches_char(char)
        ):
            return None

        return ActionIntent(
            name="wait_for_strict_route_action",
            tags={ActionTag.DEFAULT_ACTION},
            execute=lambda _: self._wait_for_strict_route_action(char, request, step),
            reason=f"waiting strict route action: {step.reason}",
        )

    def _wait_for_strict_route_action(
        self, char: "BaseChar", request: _RouteRequest, step: FollowupStep
    ) -> ActionResult:
        logger.info(f"strict route wait {char}: {request.reason} / {step.reason}")
        char.continues_normal_attack(0.15)
        return ActionResult(
            name="wait_for_strict_route_action",
            success=True,
            tags={ActionTag.DEFAULT_ACTION},
            reason=step.reason,
        )

    def _strict_route_decision(
        self, current_char: "BaseChar", context: CombatContext, has_intro: bool
    ) -> SwitchDecision | None:
        self._advance_ready_route_steps(context)
        request = self._strict_route_request(context)
        if request is None:
            return None

        step = request.current_step()
        if step is None:
            return None
        if self._route_step_blocked_by_dead_target(context, step):
            logger.warning(
                f"strict route target dead, route unlocked: {request.reason} / {step.reason}"
            )
            request.finish(RequestStatus.EXPIRED)
            request.close()
            context._state.locked_route = None
            return None

        target = self._strict_route_target(context, step)
        if target is not None:
            if step.switch_step:
                return SwitchDecision(
                    target=target,
                    reason=f"strict route switch: {request.reason} / {step.reason}",
                    priority=999999,
                    has_intro=has_intro,
                    expected_entry=None,
                    skip_switch_waits=True,
                )
            if step.requires_entry_reaction:
                if not has_intro:
                    return SwitchDecision(
                        target=current_char,
                        reason="strict route waiting entry reaction: "
                        f"{request.reason} / {step.reason}",
                        priority=999998,
                        has_intro=has_intro,
                        expected_entry=None,
                        skip_switch_waits=True,
                    )
                return SwitchDecision(
                    target=target,
                    reason=f"strict route entry reaction: {request.reason} / {step.reason}",
                    priority=999999,
                    has_intro=has_intro,
                    expected_entry=None,
                    skip_switch_waits=True,
                )
            action = self._strict_route_action(target, self._actions_for(target, context), context)
            expected = ExpectedEntry.from_action(action) if action is not None else None
            if expected is None and not step.optional:
                expected = ExpectedEntry(slot=step.slot)
            return SwitchDecision(
                target=target,
                reason=f"strict route: {request.reason} / {step.reason}",
                priority=999999,
                has_intro=has_intro,
                expected_entry=expected,
                skip_switch_waits=True,
            )

        return SwitchDecision(
            target=current_char,
            reason=f"strict route waiting: {request.reason} / {step.reason}",
            priority=999998,
            has_intro=has_intro,
            expected_entry=None,
            skip_switch_waits=True,
        )

    def _strict_route_target(self, context: CombatContext, step: FollowupStep) -> "BaseChar | None":
        for char in context.chars:
            if self._can_switch_to(char) and step.matches_char(char):
                return char
        return None

    def _route_step_blocked_by_dead_target(
        self, context: CombatContext, step: FollowupStep
    ) -> bool:
        matching_chars = [
            char for char in context.chars if char is not None and step.matches_char(char)
        ]
        return bool(matching_chars) and all(
            not self._can_switch_to(char) for char in matching_chars
        )

    def _can_switch_to(self, char: "BaseChar | None") -> bool:
        """返回 planner 是否允许把目标角色作为切人候选。"""

        return char is not None and not getattr(char, "is_dead", False)

    def _log_switch_decision(self, current_char: "BaseChar", decision: SwitchDecision):
        breakdown = (
            f", score_breakdown [{decision.score_breakdown}]" if decision.score_breakdown else ""
        )
        if decision.target == current_char:
            self._log_info_throttled(
                ("switch_keep", current_char.index, decision.reason),
                f"planner keep {current_char}, "
                f"priority {decision.priority}, reason {decision.reason}{breakdown}",
            )
        else:
            logger.info(
                f"planner switch {current_char} -> {decision.target}, "
                f"priority {decision.priority}, reason {decision.reason}{breakdown}"
            )

    def _score_action(
        self,
        char: "BaseChar",
        action: ActionIntent,
        context: CombatContext,
        breakdown: _ScoreBreakdown | None = None,
    ) -> int:
        score = self._base_action_score(action, breakdown, "action_tags")
        for request in context._state.active_requests:
            if request_fulfilled(request) and char.index == request._source:
                score += 250
                if breakdown is not None:
                    breakdown.add("request_fulfilled_source", 250)
            elif request_wants_action(request, char, action):
                score += 300
                if breakdown is not None:
                    breakdown.add("request_wants_action", 300)
        return score

    def _claims_for(self, char: "BaseChar", context: CombatContext) -> list[FieldClaim]:
        return list(self._plan_snapshot_for(char, context).claims)

    def _best_field_claim_for(self, char: "BaseChar", context: CombatContext) -> FieldClaim | None:
        claims = [claim for claim in self._claims_for(char, context) if claim.matches_char(char)]
        if not claims:
            return None
        return max(
            claims,
            key=lambda claim: (
                claim.level == FieldClaimLevel.STRICT,
                FIELD_CLAIM_SCORES.get(claim.level, 0),
            ),
        )

    def _strict_claim_decision(
        self, current_char: "BaseChar", context: CombatContext, has_intro: bool
    ) -> SwitchDecision | None:
        candidates = []
        for char in self.state.chars:
            if char == current_char or not self._can_switch_to(char):
                continue
            claim = self._best_field_claim_for(char, context)
            if claim is None or claim.level != FieldClaimLevel.STRICT:
                continue
            score, _, _, breakdown = self._score_char(char, context, current_char=False)
            candidates.append((-score, char.last_perform, char.index, char, claim, breakdown))

        if not candidates:
            return None
        _, _, _, target, claim, breakdown = min(candidates)
        return SwitchDecision(
            target=target,
            reason=f"strict field claim: {claim.reason}",
            priority=999999,
            has_intro=has_intro,
            expected_entry=None,
            score_breakdown=breakdown.format(),
            strict=True,
            skip_switch_waits=True,
        )

    def _base_action_score(
        self,
        action: ActionIntent,
        breakdown: _ScoreBreakdown | None = None,
        label: str = "action_tags",
    ) -> int:
        score = 0
        for tag in action.tags:
            score += ACTION_TAG_SCORES.get(tag, 0)
        if breakdown is not None:
            tag_label = "+".join(sorted(tag.value for tag in action.tags)) or "none"
            breakdown.add(f"{label}({tag_label})", score)
        return score

    def _score_char(
        self, char: "BaseChar", context: CombatContext, current_char: bool
    ) -> tuple[int, str, ExpectedEntry | None, _ScoreBreakdown]:
        breakdown = _ScoreBreakdown()
        action = self._best_scoring_action_for(char, context)
        field_claim = self._best_field_claim_for(char, context)
        field_action = self._field_time_action(char, context)

        if action is None and field_claim is None and field_action is None:
            breakdown.add("no_available_action", -10000)
            return -10000, "no available action", None, breakdown

        score = 0
        reason = "no ready action"
        expected = None

        if action is not None:
            score = self._score_action(char, action, context, breakdown)
            reason = action.reason or action.name
        elif field_action is not None:
            score = self._base_action_score(field_action, breakdown, "field_time_tags")
            reason = field_action.reason
            expected = None

        if field_claim is not None:
            claim_score = FIELD_CLAIM_SCORES.get(field_claim.level, 0)
            score += claim_score
            breakdown.add(f"field_claim:{field_claim.level.value}", claim_score)
            reason = f"field claim: {field_claim.reason}"
            expected = field_claim.expected_entry or expected

        if action is not None:
            for request in context._state.active_requests:
                if (
                    request.return_to_source
                    and request_fulfilled(request)
                    and char.index == request._source
                ):
                    score += 700
                    breakdown.add("return_to_source", 700)
                    reason = f"return to requester: {request.reason}"
                    expected = None
                elif request_wants_action(request, char, action):
                    score += 600
                    breakdown.add("fulfill_request", 600)
                    reason = f"fulfill request: {request.reason}"

        profile = char.describe_role()
        field_preference_score = self._field_preference_score(profile, context, current_char)
        score += field_preference_score
        breakdown.add(f"field_preference:{profile.field_preference.value}", field_preference_score)
        return score, reason, expected, breakdown

    def _field_preference_score(
        self, profile: RoleProfile, context: CombatContext, current_char: bool
    ) -> int:
        if context.has_active_request():
            request_penalty = {
                FieldPreference.MAIN_DPS: -80,
                FieldPreference.SUB_DPS: -20,
                FieldPreference.SUPPORT: 0,
                FieldPreference.SETUP_ONLY: -60,
            }
        else:
            request_penalty = {
                FieldPreference.MAIN_DPS: 120,
                FieldPreference.SUB_DPS: 40,
                FieldPreference.SUPPORT: -40,
                FieldPreference.SETUP_ONLY: -80,
            }
        score = request_penalty.get(profile.field_preference, 0)
        if current_char and profile.field_preference == FieldPreference.MAIN_DPS:
            score += 60
        return score

    def _switch_on_cooldown(self, char: "BaseChar", has_intro: bool) -> bool:
        if has_intro:
            return False
        return self.task.time_elapsed_accounting_for_freeze(char.last_switch_time) < 0.9

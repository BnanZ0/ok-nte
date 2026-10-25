from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import count
from typing import TYPE_CHECKING, Callable, Generator, Iterable, Self, cast

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

    from .context import CombatContext


class _NeverExpires:
    """`reserve_actions(until=NEVER_EXPIRES)` 使用的永久生命周期 sentinel。"""

    def __repr__(self) -> str:
        return "NEVER_EXPIRES"


class Planner:
    """PyQt-style public namespace for planner enum groups."""

    NEVER_EXPIRES = _NeverExpires()

    class Role(StrEnum):
        """角色战斗定位。

        `RoleProfile.role` 使用此枚举描述角色的队伍定位。它可用于
        `CombatContext.request_role()` 选择队友；实际站场偏好由
        `FieldPreference` 单独控制。
        """

        SUB_DPS = "Sub DPS"
        MAIN_DPS = "Main DPS"
        SUPPORT = "Support"

    class ActionTag(StrEnum):
        """动作意义标签。

        `ActionIntent.tags` 使用这些标签让 `CombatPlanner` 进行通用评分。
        标签只表达“动作价值和性质”，不要用它描述某个角色的专属机制。
        同一个 action 的 tags 是 set，重复标签不会重复加分。
        """

        DEFAULT_ACTION = "default_action"
        DAMAGE = "damage"
        ULTIMATE_ACTION = "ultimate_action"
        ARC_ACTION = "arc_action"
        SUPPORT = "support"
        TEAM_BUFF = "team_buff"
        HIGH_PRIORITY = "high_priority"
        SKILL_ACTION = "skill_action"
        FIELD_TIME = "field_time"
        LEGACY_COMBO = "legacy_combo"

    class ActionSlot(StrEnum):
        """游戏动作槽位。

        `FollowupStep` 和 `ActionReservation` 优先使用槽位协调队友动作，
        例如请求某角色释放 `SKILL`，而不是依赖具体 action name 字符串。
        """

        SKILL = "skill"
        ULTIMATE = "ultimate"
        ARC = "arc"
        ENTRY_REACTION = "entry_reaction"
        FIELD_TIME = "field_time"
        LEGACY_COMBO = "legacy_combo"
        CUSTOM = "custom"

    class FieldPreference(StrEnum):
        """Planner 用于站场评分的角色偏好。

        `RoleProfile.field_preference` 使用此枚举决定没有协作请求时谁更该站场。
        """

        MAIN_DPS = "main_dps"
        SUB_DPS = "sub_dps"
        SUPPORT = "support"
        SETUP_ONLY = "setup_only"

    class FieldClaimLevel(StrEnum):
        """角色请求入场的强度等级。

        `FieldClaim` 使用此枚举表达“这次入场诉求有多强”。具体机制原因放在
        `FieldClaim.reason` 中，避免等级名称绑定某个角色机制。
        STRICT 是硬切人诉求, 在已锁定的 strict route 之后检查。
        """

        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"
        CRITICAL = "critical"
        STRICT = "strict"

    class RequestStatus(StrEnum):
        """Planner request 的生命周期状态。

        `PENDING` 表示请求仍在进行；`FULFILLED` 表示请求已被成功满足；
        `EXPIRED` 表示请求因为 deadline、目标失效或被替换而结束。对 route
        这类有进度又有窗口生命周期的请求，`FULFILLED` 和 `EXPIRED` 可以先后发生。
        """

        PENDING = "pending"
        FULFILLED = "fulfilled"
        EXPIRED = "expired"


Role = Planner.Role
ActionTag = Planner.ActionTag
ActionSlot = Planner.ActionSlot
FieldPreference = Planner.FieldPreference
FieldClaimLevel = Planner.FieldClaimLevel
RequestStatus = Planner.RequestStatus
NEVER_EXPIRES = Planner.NEVER_EXPIRES


ACTION_TAG_SCORES = {
    ActionTag.DEFAULT_ACTION: 10,
    ActionTag.DAMAGE: 35,
    ActionTag.ULTIMATE_ACTION: 200,
    ActionTag.ARC_ACTION: 0,
    ActionTag.SUPPORT: 45,
    ActionTag.TEAM_BUFF: 260,
    ActionTag.HIGH_PRIORITY: 160,
    ActionTag.SKILL_ACTION: 75,
    ActionTag.FIELD_TIME: 40,
    ActionTag.LEGACY_COMBO: 45,
}


FIELD_CLAIM_SCORES = {
    FieldClaimLevel.LOW: 120,
    FieldClaimLevel.NORMAL: 240,
    FieldClaimLevel.HIGH: 420,
    FieldClaimLevel.CRITICAL: 700,
}


@dataclass(slots=True)
class RequestHandle:
    """角色代码可用来观察 request 生命周期的轻量句柄。

    `request_route()`、`reserve_actions()`、`request_switch()` 和
    `request_tags()` 会返回此对象。它不控制 request 本身，只让角色代码查询
    request 当前是否仍在进行，以及把 request 的生命周期信号组合成其他 request 的
    `until` 条件。
    """

    _fulfilled: bool = False
    _expired: bool = False
    _closed: bool = False
    _on_finish: list[Callable[[], None]] = field(default_factory=list)
    _on_fulfilled: list[Callable[[], None]] = field(default_factory=list)
    _on_expired: list[Callable[[], None]] = field(default_factory=list)

    @property
    def status(self) -> RequestStatus:
        """当前摘要状态。

        普通场景优先使用 `is_pending`、`is_fulfilled`、`is_expired`、
        `is_closed` 或 `when.*`。如果请求先 fulfilled 后 expired，此属性
        返回 `EXPIRED`，但 `is_fulfilled` 仍会保持 True。`closed` 是独立的
        planner 影响面信号，不由 `RequestStatus` 表达。
        """

        if self._expired:
            return RequestStatus.EXPIRED
        if self._fulfilled:
            return RequestStatus.FULFILLED
        return RequestStatus.PENDING

    @property
    def is_pending(self) -> bool:
        """请求是否尚未出现 fulfilled、expired 或 closed 信号。

        `True` 表示 request 还没有成功完成，没有过期/失效，也还没有从
        planner 影响面中移除。
        """

        return not self._fulfilled and not self._expired and not self._closed

    @property
    def is_fulfilled(self) -> bool:
        """请求是否已被成功满足。

        例如 route 的所有步骤都完成、switch request 已切到目标、tag request
        已收到足够动作。fulfilled 不会阻止后续 expired 信号发生。
        """

        return self._fulfilled

    @property
    def is_expired(self) -> bool:
        """请求是否已过期或失效。

        例如 `until()` 返回 True、目标角色失效，或旧 strict route 被新的
        strict route 覆盖。expired 不会清除已经发生的 fulfilled 信号。
        """

        return self._expired

    @property
    def is_closed(self) -> bool:
        """请求是否已经不再影响 planner。

        closed 表示 request 已经离开 strict route 锁、active request 列表，
        不再影响切人、动作许可或 return-to-source。closed 不代表生命周期
        deadline 已经过期；例如 route fulfilled 后可能先 closed，再等窗口
        until 触发 expired。
        """

        return self._closed

    @property
    def when(self) -> "RequestWhenPredicates":
        """返回可直接传给 `until=` 的生命周期条件集合。

        `when` 不是一个状态；它是 predicate namespace：
        `when.fulfilled` 表示“成功完成时结束”，`when.expired` 表示
        “过期时结束”，`when.closed` 表示“不再影响 planner 时结束”，
        `when.any` 表示“任一生命周期信号出现就结束”。
        """

        return RequestWhenPredicates(self)

    def on_finish(self, callback: Callable[[], None]) -> "RequestHandle":
        """注册首次结束信号回调。

        request 首次进入 `FULFILLED` 或 `EXPIRED` 时调用一次。若注册时 request
        已经出现过任一信号，callback 会立即执行一次。需要分别处理成功和过期时，
        使用 `on_fulfilled()` / `on_expired()`。
        """

        if self.is_pending:
            self._on_finish.append(callback)
        else:
            callback()
        return self

    def on_fulfilled(self, callback: Callable[[], None]) -> "RequestHandle":
        """注册成功完成回调。

        只在 request 进入 `FULFILLED` 时调用。若注册时已经 fulfilled，callback
        会立即执行。
        """

        if self.is_fulfilled:
            callback()
        else:
            self._on_fulfilled.append(callback)
        return self

    def on_expired(self, callback: Callable[[], None]) -> "RequestHandle":
        """注册过期/失效回调。

        只在 request 进入 `EXPIRED` 时调用。若注册时已经 expired，callback
        会立即执行。即使 request 已经 fulfilled，也可以继续等待后续 expired。
        """

        if self.is_expired:
            callback()
        else:
            self._on_expired.append(callback)
        return self

    def _finish(self, status: RequestStatus) -> bool:
        first_signal = self.is_pending
        callbacks = []
        if status is RequestStatus.FULFILLED:
            if self._fulfilled:
                return False
            self._fulfilled = True
            callbacks.extend(self._on_fulfilled)
            self._on_fulfilled.clear()
        elif status is RequestStatus.EXPIRED:
            if self._expired:
                return False
            self._expired = True
            callbacks.extend(self._on_expired)
            self._on_expired.clear()
        else:
            return False
        if first_signal:
            callbacks = list(self._on_finish) + callbacks
            self._on_finish.clear()
        for callback in callbacks:
            callback()
        return first_signal

    def _close(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        return True


@dataclass(frozen=True, slots=True)
class RequestWhenPredicates:
    """`RequestHandle.when` 下的生命周期 predicate 集合。

    这些属性返回 `Callable[[], bool]`，用于传给另一个 request 的 `until=...`。
    """

    _handle: RequestHandle

    @property
    def any(self) -> Callable[[], bool]:
        """返回“request fulfilled、expired 或 closed 后为 True”的条件。"""

        return lambda: (
            self._handle.is_fulfilled or self._handle.is_expired or self._handle.is_closed
        )

    @property
    def fulfilled(self) -> Callable[[], bool]:
        """返回“request 成功完成时为 True”的条件。"""

        return lambda: self._handle.is_fulfilled

    @property
    def expired(self) -> Callable[[], bool]:
        """返回“request 过期或失效时为 True”的条件。"""

        return lambda: self._handle.is_expired

    @property
    def closed(self) -> Callable[[], bool]:
        """返回“request 不再影响 planner 时为 True”的条件。"""

        return lambda: self._handle.is_closed


@dataclass(slots=True)
class RoleProfile:
    """角色向 `CombatPlanner` 声明的基础战斗画像。

    由角色的 `describe_role()` 返回。`role` 描述队伍定位, 可由
    `CombatContext.request_role()` 匹配; `field_preference` 决定普通切人评分。
    未指定 `field_preference` 时根据 `role` 补全, 显式配置始终优先。
    `max_field_time` 会被 planner 用来生成内建的 `planner_field_time` 站场动作。

    `combat_start_priority` 只用于开战首切。大于 0 的角色会成为首切候选；
    数值越高越优先。普通战斗中的切人评分不会使用此字段。
    """

    role: Role = Role.SUB_DPS
    # None is an initialization sentinel, resolved before the profile is returned.
    field_preference: FieldPreference = cast(FieldPreference, None)
    max_field_time: float = 1.5
    combat_start_priority: int = 0

    def __post_init__(self) -> None:
        if self.field_preference is None:
            self.field_preference = {
                Role.MAIN_DPS: FieldPreference.MAIN_DPS,
                Role.SUB_DPS: FieldPreference.SUB_DPS,
                Role.SUPPORT: FieldPreference.SUPPORT,
            }[self.role]


@dataclass(slots=True)
class FieldClaim:
    """角色向 planner 声明“我应该被切进来”的理由。

    LOW 到 CRITICAL 提高普通入场评分。STRICT 在已锁定的 strict route 之后,
    于下一次切人决策中直接选择该角色, 切入后走普通 entry 流程。
    """

    _source: int = -1
    level: FieldClaimLevel = FieldClaimLevel.NORMAL
    reason: str = ""
    expected_entry: "ExpectedEntry | None" = None

    @classmethod
    def low(
        cls,
        source: "BaseChar | str | None" = None,
        reason: str = "",
        expected_entry: "ExpectedEntry | None" = None,
    ) -> "FieldClaim":
        """声明低强度入场诉求。"""

        return cls._from_source(source, FieldClaimLevel.LOW, reason, expected_entry)

    @classmethod
    def normal(
        cls,
        source: "BaseChar | str | None" = None,
        reason: str = "",
        expected_entry: "ExpectedEntry | None" = None,
    ) -> "FieldClaim":
        """声明普通强度入场诉求。"""

        return cls._from_source(source, FieldClaimLevel.NORMAL, reason, expected_entry)

    @classmethod
    def high(
        cls,
        source: "BaseChar | str | None" = None,
        reason: str = "",
        expected_entry: "ExpectedEntry | None" = None,
    ) -> "FieldClaim":
        """声明高强度入场诉求。"""

        return cls._from_source(source, FieldClaimLevel.HIGH, reason, expected_entry)

    @classmethod
    def critical(
        cls,
        source: "BaseChar | str | None" = None,
        reason: str = "",
        expected_entry: "ExpectedEntry | None" = None,
    ) -> "FieldClaim":
        """声明最高强度入场诉求。"""

        return cls._from_source(source, FieldClaimLevel.CRITICAL, reason, expected_entry)

    @classmethod
    def strict(
        cls,
        source: "BaseChar | str | None" = None,
        reason: str = "",
    ) -> "FieldClaim":
        """要求在下一次切人决策时切入该角色, 切入后执行普通 entry。"""

        return cls._from_source(source, FieldClaimLevel.STRICT, reason)

    @classmethod
    def _from_source(
        cls,
        source: "BaseChar | str | None",
        level: FieldClaimLevel,
        reason: str = "",
        expected_entry: "ExpectedEntry | None" = None,
    ) -> "FieldClaim":
        if isinstance(source, str) and not reason:
            reason = source
            source = None
        source_char = source
        source_id = source_char.index if source_char is not None else -1
        if source_char is not None and not reason:
            reason = cls.default_reason(source_char, level)
        return cls(
            _source=source_id,
            level=level,
            reason=reason,
            expected_entry=expected_entry,
        )

    @staticmethod
    def default_reason(source: "BaseChar", level: FieldClaimLevel) -> str:
        return f"{source} uses FieldClaim {level.value}"

    def ensure_source(self, char: "BaseChar") -> None:
        """如果 claim 未声明来源，使用当前发布 claim 的角色作为来源。"""

        if self._source < 0:
            self._source = char.index
        if not self.reason:
            self.reason = self.default_reason(char, self.level)

    def matches_char(self, char: "BaseChar") -> bool:
        """判断此 claim 是否属于目标角色。"""

        return char.index == self._source


@dataclass(slots=True)
class SwitchInGuard:
    """目标角色切入前的保护条件。

    由即将被切入的角色声明“现在是否适合让我进场”。这避免当前角色替目标角色
    判断入场时机，也让 guard 的语义稳定为“延迟目标入场”，而不是“阻止当前切出”。
    """

    delay_until: Callable[[], bool] | None = None
    timeout: float = 0.0
    reason: str = ""
    poll_interval: float = 0.05
    while_waiting: Callable[[], None] | None = None

    @classmethod
    def allow(cls) -> "SwitchInGuard":
        """允许立即切入。"""

        return cls()

    @classmethod
    def delay_until_ready(
        cls,
        condition: Callable[[], bool],
        timeout: float,
        reason: str = "",
        poll_interval: float = 0.05,
        while_waiting: Callable[[], None] | None = None,
    ) -> "SwitchInGuard":
        """延迟切入直到 `condition()` 为 True 或 timeout 到期。"""

        return cls(
            delay_until=condition,
            timeout=timeout,
            reason=reason,
            poll_interval=poll_interval,
            while_waiting=while_waiting,
        )

    def should_delay(self) -> bool:
        """返回当前是否仍需要延迟切入。"""

        return self.delay_until is not None and not self.delay_until()


@dataclass(slots=True)
class ActionResult:
    """一次动作执行后的标准结果。"""

    name: str = ""
    success: bool = True
    tags: set[ActionTag] = field(default_factory=set)
    slot: ActionSlot | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        """让 entry flow 可以用普通 Python truthiness 判断动作是否成功。"""

        return self.success


ActionExecutor = Callable[["CombatContext"], ActionResult | bool | None]
ActionPredicate = Callable[["CombatContext"], bool]
EntryFlow = Generator["ActionIntent", ActionResult, None]
EntryFactory = Callable[[], EntryFlow]


_ENTRY_REPEAT_IDS = count()


@dataclass(slots=True)
class ActionIntent:
    """角色声明给 `CombatPlanner` 的候选动作。"""

    tags: set[ActionTag]
    execute: ActionExecutor
    name: str = ""
    slot: ActionSlot | None = None
    reason: str = ""
    can_execute: ActionPredicate | None = None
    priority_ready: ActionPredicate | None = None
    _entry_repeat_id: int | None = field(default=None, repr=False, compare=False)

    def identity_key(self) -> str:
        """返回 planner 内部使用的动作身份。"""

        if self.name:
            identity = f"name:{self.name}"
        elif self.slot is not None:
            if self.reason:
                identity = f"slot:{self.slot}|reason:{self.reason}"
            else:
                identity = f"slot:{self.slot}"
        else:
            tag_key = ",".join(sorted(str(tag) for tag in self.tags))
            identity = f"tags:{tag_key}|reason:{self.reason}"
        if self._entry_repeat_id is not None:
            return f"{identity}|entry_repeat:{self._entry_repeat_id}"
        return identity

    def repeat_for_entry(self) -> Self:
        """返回可在同一次 entry 中再次 yield 的动作副本。

        副本保留原 action 的执行器, 槽位, 标签和权限限制, 仅增加 entry 内部的
        去重标识。每次调用都会生成一个新的标识, 因此循环中可以自然重试动作。
        它适合大招窗口内再次施放 E 这类机制; 通常只在 entry flow 里 yield,
        不加入 `CombatPlan.actions`, 因此不参与切人评分。
        """

        return replace(self, _entry_repeat_id=next(_ENTRY_REPEAT_IDS))

    def display_name(self) -> str:
        """返回仅用于日志的人类可读动作名。"""

        if self.name:
            return self.name
        if self.slot is not None:
            return f"<{self.slot.value}>"
        if self.reason:
            return f"<{self.reason}>"
        return "<anonymous_action>"

    def is_allowed(self, context: "CombatContext") -> bool:
        """返回 planner 层是否允许该动作执行。"""

        if self.can_execute is None:
            return True
        return self.can_execute(context)

    def is_priority_ready(self, context: "CombatContext") -> bool:
        """返回该动作当前是否值得参与评分和切人。"""

        if not self.is_allowed(context):
            return False
        if self.priority_ready is None:
            return True
        return self.priority_ready(context)

    def run(self, context: "CombatContext") -> ActionResult:
        """执行动作并标准化为 `ActionResult`。"""

        result = self.execute(context)
        if isinstance(result, ActionResult):
            if not result.name and self.name:
                result.name = self.name
            if not result.tags:
                result.tags.update(self.tags)
            if result.slot is None:
                result.slot = self.slot
            action_result = result
        else:
            action_result = ActionResult(
                name=self.name,
                success=result is True,
                tags=set(self.tags),
                slot=self.slot,
            )
        return action_result


@dataclass(slots=True)
class CombatPlan:
    """角色一次 planner 查询的完整计划。

    `actions` 是 planner 可见的动作目录，用于切人评分、route、request 和 reservation。
    `claims` 是入场诉求。`entry` 是普通入场后的 Python generator 流程；未提供时
    planner 会按 `actions` 声明顺序执行。
    """

    actions: Iterable[ActionIntent] = field(default_factory=tuple)
    claims: Iterable[FieldClaim] = field(default_factory=tuple)
    entry: EntryFactory | None = None

    def __post_init__(self) -> None:
        self.actions = list(self.actions)
        self.claims = list(self.claims)

    def add[T: ActionIntent | FieldClaim | None](self, item: T) -> T:
        """加入动作或入场诉求并返回原对象; None 会被忽略。

        在创建 plan 时调用, 不要在 entry 执行期间修改动作目录。
        动作按 add 顺序加入目录, 不会立即执行。
        """

        if isinstance(item, ActionIntent):
            cast(list[ActionIntent], self.actions).append(item)
        elif isinstance(item, FieldClaim):
            cast(list[FieldClaim], self.claims).append(item)
        elif item is not None:
            raise TypeError("CombatPlan.add() expects an ActionIntent, FieldClaim, or None")
        return item


def _display_result_name(result: ActionResult) -> str:
    if result.name:
        return result.name
    if result.slot is not None:
        return f"<{result.slot.value}>"
    if result.reason:
        return f"<{result.reason}>"
    return "<anonymous_action>"


@dataclass(slots=True)
class FollowupStep:
    """`CombatContext.request_route()` 中的一步协作要求。"""

    reason: str
    slot: ActionSlot | None = None
    required_tags: set[ActionTag] = field(default_factory=set)
    action_names: set[str] = field(default_factory=set)
    target_indices: set[int] = field(default_factory=set)
    target_names: set[str] = field(default_factory=set)
    requires_entry_reaction: bool = False
    optional: bool = False

    switch_step: bool = False
    wait_for_turn: bool = False

    @classmethod
    def for_switch(
        cls, target: "BaseChar", reason: str = "", *, wait_for_turn: bool = True
    ) -> "FollowupStep":
        """创建切入指定角色的 route 步骤, 可等待其完成一轮正常出招流程。

        Args:
            target: 要切入的目标角色。
            reason: 日志和调试用理由; 为空时根据目标角色自动生成。
            wait_for_turn: 是否等待目标执行完本轮后再完成步骤, 默认 True。
                True 时, 切入后按目标的正常 entry 流程执行一轮; 目标已在场时
                也要执行本轮。False 时, 切入成功或目标已在场即完成步骤,
                立即推进 route, 不保证目标执行任何动作。

        Returns:
            供 CombatContext.request_route() 使用的切人步骤。
        """

        return cls(
            reason=reason or f"{target} switch followup",
            target_indices={target.index},
            switch_step=True,
            wait_for_turn=wait_for_turn,
        )

    @classmethod
    def for_action(
        cls,
        target: "BaseChar",
        slot: ActionSlot,
        reason: str = "",
        optional: bool = False,
        required_tags: set[ActionTag] | None = None,
        action_names: set[str] | None = None,
    ) -> "FollowupStep":
        """创建“指定角色执行指定槽位动作”的 strict route 步骤。"""

        return cls(
            reason=reason or f"{target} {slot} followup",
            slot=slot,
            required_tags=required_tags or set(),
            action_names=action_names or set(),
            target_indices={target.index},
            optional=optional,
        )

    @classmethod
    def for_entry_reaction(
        cls,
        target: "BaseChar",
        reason: str = "",
        optional: bool = False,
    ) -> "FollowupStep":
        """创建“切入目标角色触发入场/环合反应”的 strict route 步骤。"""

        return cls(
            reason=reason or f"{target} entry reaction followup",
            slot=ActionSlot.ENTRY_REACTION,
            target_indices={target.index},
            requires_entry_reaction=True,
            optional=optional,
        )

    def matches_char(self, char: "BaseChar") -> bool:
        """判断角色是否符合此步骤的目标条件。"""

        if self.target_indices and char.index not in self.target_indices:
            return False
        if self.target_names:
            names = {char.name, str(getattr(char, "char_name", ""))}
            if not self.target_names.intersection(names):
                return False
        return True

    def wants(self, char: "BaseChar", action: ActionIntent | ActionResult) -> bool:
        """判断某角色动作是否满足此步骤。"""

        if self.switch_step or self.requires_entry_reaction:
            return False
        if not self.matches_char(char):
            return False
        if self.action_names and action.name not in self.action_names:
            return False
        if self.slot is not None and action.slot != self.slot:
            return False
        if self.required_tags and not self.required_tags.intersection(action.tags):
            return False
        return bool(self.action_names or self.slot is not None or self.required_tags)

    def wants_entry_reaction(self, source_char: "BaseChar", target_char: "BaseChar") -> bool:
        """判断一次入场/环合反应是否满足此步骤。"""

        if not self.requires_entry_reaction:
            return False
        return self.matches_char(target_char)


@dataclass(slots=True)
class ActionReservation:
    """planner 层的动作保留。"""

    slots: set[ActionSlot]
    target_indices: set[int] = field(default_factory=set)
    target_names: set[str] = field(default_factory=set)

    @classmethod
    def for_action(
        cls,
        target: "BaseChar",
        slot: ActionSlot,
    ) -> "ActionReservation":
        """创建单一目标角色、单一动作槽位的保留。"""

        return cls(slots={slot}, target_indices={target.index})

    @classmethod
    def for_slots(
        cls,
        target: "BaseChar",
        slots: Iterable[ActionSlot],
    ) -> "ActionReservation":
        """创建单一目标角色、多个动作槽位的保留。"""

        return cls(slots=set(slots), target_indices={target.index})

    def matches_char(self, char: "BaseChar") -> bool:
        """判断角色是否是此保留的目标。"""

        if self.target_indices and char.index not in self.target_indices:
            return False
        if self.target_names:
            names = {char.name, str(getattr(char, "char_name", ""))}
            if not self.target_names.intersection(names):
                return False
        return True

    def reserves_action(self, char: "BaseChar", action: ActionIntent | ActionResult) -> bool:
        """判断此保留是否会阻止某角色动作。"""

        if not self.matches_char(char):
            return False
        return action.slot in self.slots


@dataclass(slots=True)
class SwitchDecision:
    """planner 的切人决策结果。

    `CombatPlanner.decide_switch()` 返回此类型，执行端根据 `target` 尝试切人，
    并在确认成功后将最终决策交给 `CombatPlanner.complete_switch()`。planner 会
    根据 `expected_entry` 登记切入后优先尝试的动作。
    `strict` 表示 strict claim 的决策。`skip_switch_waits` 表示本次切人跳过
    `SwitchInGuard` 和切人冷却等待, 由 planner 在决策时确定。
    """

    target: "BaseChar"
    reason: str
    priority: int
    has_intro: bool = False
    expected_entry: "ExpectedEntry | None" = None
    score_breakdown: str = ""
    strict: bool = False
    skip_switch_waits: bool = False


@dataclass(slots=True)
class ExpectedEntry:
    """切入目标角色后应优先尝试的动作期望。

    claim 或 strict route 可以设置入场动作期望。
    """

    slot: ActionSlot | None = None
    action_name: str = ""

    @classmethod
    def from_action(cls, action: ActionIntent) -> "ExpectedEntry":
        """从 `ActionIntent` 创建切入期望。"""

        return cls(slot=action.slot, action_name=action.name)

    def matches(self, action: ActionIntent) -> bool:
        """判断动作是否符合此切入期望。"""

        if self.action_name and action.name != self.action_name:
            return False
        if self.slot is not None and action.slot != self.slot:
            return False
        return bool(self.action_name or self.slot is not None)

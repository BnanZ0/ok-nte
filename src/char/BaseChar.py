from dataclasses import dataclass
from enum import Enum, IntFlag, StrEnum, auto
from typing import TYPE_CHECKING, Callable, Concatenate

from ok import Logger
from typing_extensions import deprecated

from src import text_white_color
from src.combat.planner import (
    ActionIntent,
    ActionPredicate,
    ActionResult,
    CombatContext,
    CombatPlan,
    FieldClaim,
    Planner,
    RoleProfile,
    SwitchInGuard,
)
from src.combat.planner.compatibility import (
    _BoundAction,
    adapt_character_hooks,
    custom_action,
    legacy_context_hook,
    native_action,
)
from src.Labels import Labels
from src.utils import clock
from src.utils import game_filters as gf

if TYPE_CHECKING:
    from src.combat.BaseCombatTask import BaseCombatTask

SKILL_TIME_OUT = 15


class _AvailableActionStatus(Enum):
    CONTINUE = auto()
    UNAVAILABLE = auto()
    RELEASED = auto()
    ANIMATION = auto()
    TIMEOUT = auto()


@dataclass(slots=True)
class _AvailableActionResult:
    clicked: bool = False
    action_time: clock.TimePoint = clock.UNSET
    animation_start: clock.TimePoint = clock.UNSET
    status: _AvailableActionStatus = _AvailableActionStatus.UNAVAILABLE
    timed_out: bool = False


class Element(StrEnum):
    """定义角色元素枚举。"""

    DEFAULT = "Default"  # 默认/未知元素
    BLUE = "Blue"  # 蓝
    GREEN = "Green"  # 绿
    RED = "Red"  # 红
    PURPLE = "Purple"  # 紫
    YELLOW = "Yellow"  # 黄
    WHITE = "White"  # 白


class BaseChar:
    """角色基类，定义了游戏角色的通用属性和行为。"""

    class SleepChecks(IntFlag):
        """Checks that can run while a character sleeps."""

        NONE = 0
        COMBAT = auto()
        SOUND = auto()
        ALL = COMBAT | SOUND

    ElementType = Element
    clock = clock
    _combat_context: CombatContext | None = None
    INTRO_MOTION_FREEZE_DURATION = 1.5
    en_name = ""
    cn_name = ""
    element = Element.DEFAULT

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        adapt_character_hooks(cls)
        cls.en_name = cls.__dict__.get("en_name", "") or cls.__name__
        cls.cn_name = cls.__dict__.get("cn_name", "") or cls.en_name

    def __init__(self, task, index: int, char_id="", confidence=1):
        """初始化角色基础属性。

        Args:
            task (BaseCombatTask): 所属的战斗任务对象。
            index (int): 角色在队伍中的索引 (0, 1, 2)。
            char_name (str, optional): 角色名称。默认为 None。
        """
        self.task: "BaseCombatTask" = task
        self.char_name = "default"
        self.char_id = char_id
        self.impl_id = ""
        self.index = index
        self.last_switch_time = self.clock.UNSET
        self.last_ultimate_time = self.clock.UNSET
        self.has_intro = False
        self.is_current_char = False
        self._ultimate_available = False
        self._skill_available = False
        self.last_perform = self.clock.UNSET
        self.last_skill_time = self.clock.UNSET
        self.last_outro_time = self.clock.UNSET
        self.confidence = confidence
        self.logger = Logger.get_logger(self.name)
        self.cycle_start_time = self.clock.UNSET
        self.element = type(self).element
        self.planner_handles_arc = False
        self.is_dead = False

    def cycle_start(self):
        self.cycle_start_time = self.clock.now()

    def cycle_sleep(self, duration=0.1):
        to_sleep = duration - (self.clock.now() - self.cycle_start_time)
        if to_sleep > 0.05:
            self.check_combat()
        self.sleep(duration - (self.clock.now() - self.cycle_start_time))

    def skip_combat_check(self):
        """是否在某些操作中跳过战斗状态检查。

        Returns:
            bool: 如果跳过则返回 True。
        """
        return False

    def has_element_reaction_teammate(self) -> bool:
        """当前队伍中是否有可以和自己形成环合反应的角色。"""

        return self.task.find_element_reaction_target(self) is not None

    @property
    def name(self):
        """获取角色类名作为其名称。

        Returns:
            str: 角色类名字符串。
        """
        return f"{self.__class__.__name__}"

    @property
    def ufn_name(self) -> str:
        """获取用户友好的角色名称。

        优先使用用户为当前角色实例配置的名称; 未初始化或未知时回退到角色元数据。
        """
        char_name = str(self.char_name).strip()
        if char_name and char_name not in {"default", "unknown"}:
            return char_name
        _name = type(self).cn_name if self.task.is_chinese() else type(self).en_name
        return _name or self.name

    def __eq__(self, other):
        """比较两个角色对象是否相同 (基于名称和索引)。"""
        if isinstance(other, BaseChar):
            return self.name == other.name and self.index == other.index
        return False

    @property
    def ultimate_freeze_wait(self) -> None | float:
        return None

    def perform(self):
        """执行当前角色的主要战斗行动序列。"""
        self.last_perform = self.clock.now()
        self.task.record_first_engage(self)
        if self.has_intro:
            self.add_intro_motion_freeze(self.last_perform)
            self.wait_intro()
        self._try_default_arc_click()

        self.task.combat_planner.perform_current_char(self)
        self.logger.debug(f"set current char false {self.index}")
        self.task.refresh_cd()
        self.switch_next_char()

    def _try_default_arc_click(self):
        if not self.planner_handles_arc:
            self.click_arc()

    def add_intro_motion_freeze(self, start):
        self.add_freeze_duration(start, self.INTRO_MOTION_FREEZE_DURATION, freeze_time=-100)

    def wait_intro(self, time_out=-1, click=True):
        """等待角色入场动画结束。

        Args:
            time_out (float, optional): 等待超时时间 (秒)。默认为 1.2。
            click (bool, optional): 等待期间是否持续点击。默认为 True。
        """
        if time_out < 0:
            time_out = self.INTRO_MOTION_FREEZE_DURATION

        if self.has_intro:
            self.logger.info(f"wait intro {time_out}s")
            if click:
                self.continues_normal_attack(time_out)
            else:
                self.sleep(time_out)
            self.logger.info("wait intro end")

    def click_with_interval(self, interval=0.1):
        """以指定间隔执行点击操作。

        Args:
            interval (float, optional): 点击间隔。默认为 0.1。
        """
        self.click(interval=interval)

    @property
    def click(self):
        """执行一次点击操作 (代理到 task.click)。"""
        return self.task.click

    @property
    def send_key(self):
        """发送按键 (代理到 task.send_key)。"""
        return self.task.send_key

    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS)

    @legacy_context_hook
    def switch_in_guard(
        self,
        from_char: "BaseChar",
        has_intro: bool,
    ) -> SwitchInGuard:
        """声明当前角色是否允许被切入。

        默认立即允许。特殊角色若需要等自身状态或前置动作稳定后再进场，可返回
        `SwitchInGuard.delay_until_ready(...)`。
        """

        return SwitchInGuard.allow()

    @legacy_context_hook
    def combat_plan(self) -> CombatPlan:
        """声明角色交给 planner 的完整战斗计划。

        `CombatPlan.actions` 表达“进场后有哪些 planner 可见动作”，用于评分、
        route/request/reservation 匹配；`claims` 表达“为什么应该被切进来”；
        `entry` 表达普通进场后的 Python 动作流。

        规则:
            - 这里只创建 plan，不要在创建 plan 时发送输入或发布一次性请求。
            - 普通进场默认按 actions 声明顺序尝试 allowed action。
            - 切人评分会从该角色 ready actions 中挑最高分 action 代表角色参赛。
            - 复杂角色可传入普通 entry 方法或 generator, 用 Python 控制出招分支。

        Returns:
            `CombatPlan`。
        """

        plan = self.plan()
        self.register_ultimate(plan, name="base_ultimate")
        self.register_skill(plan, name="base_skill")
        return plan

    @legacy_context_hook
    def combat_policies(self) -> None:
        """声明随队伍生命周期生效的 planner 策略。

        这里适合发布常驻 reservation 这类长期策略。普通角色通常不需要覆盖。
        `combat_plan()` 应保持为动作/入场诉求声明，不要在评分扫描时发布请求。

        planner reset 当前队伍后会调用此方法。适合发布由队伍组成决定的长期规则，
        不适合发布“本次 Q/E 成功后才出现”的临时窗口。

        通过 self.combat_context 发布长期策略。
        """

        return None

    @property
    def combat_context(self) -> CombatContext:
        """返回当前规划、策略或执行阶段的上下文, 无需在角色方法间传递。"""

        if self._combat_context is None:
            raise RuntimeError("combat_context requires an active planner cycle")
        self._combat_context._planner._validate_context(self._combat_context)
        return self._combat_context

    def plan(
        self,
        *actions: ActionIntent | None,
        claims: list[FieldClaim] | tuple[FieldClaim, ...] | None = None,
        entry=None,
    ) -> CombatPlan:
        """创建角色 `CombatPlan`, 并过滤空动作。

        用法:
            return self.plan(action_or_none, claims=[claim], entry=entry)

        也可以先创建空 plan, 用 self.register_skill(plan, ...) 登记动作,
        用 plan.add(claim) 声明入场诉求, 设置 plan.entry 后直接返回 plan。
        """

        return CombatPlan(
            actions=[action for action in actions if action is not None],
            claims=[claim for claim in claims or [] if claim is not None],
            entry=entry,
        )

    def register_skill(
        self,
        plan: CombatPlan,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "skill action available",
        can_execute: Callable[[], bool] | None = None,
        priority_ready: Callable[[], bool] | None = None,
    ) -> None:
        """登记 E 的槽位、默认标签和就绪检查, 不执行或返回动作。

        tags 替换默认 SKILL_ACTION, add_tags 追加标签。每次执行均检查技能
        可用性和 can_execute; priority_ready 仅影响切人评分, 默认检查技能就绪。
        实际释放参数直接传给 click_skill()。
        """

        condition = can_execute
        plan.register(
            self.click_skill,
            name=name or f"{self}_skill",
            slot=Planner.ActionSlot.SKILL,
            tags={Planner.ActionTag.SKILL_ACTION} if tags is None else tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=lambda: self.skill_available() and (condition is None or condition()),
            priority_ready=priority_ready,
        )

    def register_ultimate(
        self,
        plan: CombatPlan,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "ultimate action available",
        can_execute: Callable[[], bool] | None = None,
        priority_ready: Callable[[], bool] | None = None,
    ) -> None:
        """登记 Q 的槽位、默认标签和就绪检查, 不执行或返回动作。

        tags 替换默认 ULTIMATE_ACTION, add_tags 追加标签。每次执行均检查
        终结技可用性和 can_execute; priority_ready 默认检查终结技就绪。
        实际释放参数直接传给 click_ultimate()。
        """

        condition = can_execute
        plan.register(
            self.click_ultimate,
            name=name or f"{self}_ultimate",
            slot=Planner.ActionSlot.ULTIMATE,
            tags={Planner.ActionTag.ULTIMATE_ACTION} if tags is None else tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=lambda: self.ultimate_available() and (condition is None or condition()),
            priority_ready=priority_ready,
        )

    def register_arc(
        self,
        plan: CombatPlan,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "arc action available",
        can_execute: Callable[[], bool] | None = None,
        priority_ready: Callable[[], bool] | None = None,
    ) -> None:
        """登记弧盘的槽位和默认 ARC_ACTION 标签, 默认不因弧盘主动切人。

        tags 替换默认标签, add_tags 追加标签。priority_ready 仅影响切人评分;
        已在场时仍可执行弧盘。实际释放参数直接传给 click_arc()。
        """

        plan.register(
            self.click_arc,
            name=name or f"{self}_arc",
            slot=Planner.ActionSlot.ARC,
            tags={Planner.ActionTag.ARC_ACTION} if tags is None else tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=can_execute,
            priority_ready=priority_ready or (lambda: False),
        )

    @deprecated("Use self.register_arc(plan, ...)", category=None)
    def click_arc_action(
        self,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "arc action available",
        can_execute=None,
        priority_ready: ActionPredicate | None = None,
    ):
        """创建一个 弧盘 动作声明。

        Args:
            name: 动作名。默认 `"{角色名}_arc"`，用于日志和高级精确匹配。
            tags: 动作标签。默认 `{Planner.ActionTag.ARC_ACTION}`。
            reason: 切人/执行日志理由。
            can_execute: 额外硬限制；slot reservation 由 planner 统一检查。
            priority_ready: 只用于切人评分。默认永远不因为 arc 主动切人。

        Returns:
            `ActionIntent`。
        """

        action = native_action(
            self.click_arc,
            name=name or f"{self}_arc",
            tags=tags or {Planner.ActionTag.ARC_ACTION},
            add_tags=add_tags,
            slot=Planner.ActionSlot.ARC,
            reason=reason,
            can_execute=can_execute,
            priority_ready=priority_ready or (lambda _: False),
        )
        return action

    @deprecated("Use self.register_ultimate(plan, ...)", category=None)
    def click_ultimate_action(
        self,
        plan: CombatPlan | None = None,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "ultimate action available",
        can_execute: ActionPredicate | None = None,
    ):
        """创建一个 Q 动作声明, 可选择加入指定计划。

        Args:
            plan: 要加入的计划。传入时自动追加动作; 不传时只创建动作声明。
            name: 动作名; 默认 "{角色名}_ultimate", 用于日志和 planner 精确匹配。
            tags: 替换默认动作标签; 默认为 {Planner.ActionTag.ULTIMATE_ACTION}。
            add_tags: 在最终标签上追加一个或多个标签。
            reason: planner 选择或执行此动作时使用的说明。
            can_execute: 接收 CombatContext 的额外条件; 返回 False 时禁止执行。
                终结技本身不可用时, 动作仍会被禁止。

        Returns:
            保留 click_ultimate 参数提示的动作声明。直接 yield ultimate 使用已有参数;
            yield ultimate(send_click=False) 为本次释放指定参数。

        Behavior:
            - 创建动作和绑定参数都不会发送输入, 动作交给 planner 后才执行。
            - 自动设置 slot=Planner.ActionSlot.ULTIMATE。
            - 执行前检查 ultimate_available() 和额外的 can_execute 条件。
            - 切人评分使用 ultimate_available() 判断终结技是否就绪。
            - planner 根据动作槽位和 reservation 检查是否允许释放。
            - 固定执行 self.click_ultimate(...), 不接受 execute 替换。
            - send_click、wait_if_no_cd 等释放参数写在返回动作的调用处,
              含义及默认值见 click_ultimate()。

        Examples:
            ultimate = self.click_ultimate_action(plan)
            yield ultimate(send_click=False)
        """
        action = native_action(
            self.click_ultimate,
            name=name or f"{self}_ultimate",
            tags=tags or {Planner.ActionTag.ULTIMATE_ACTION},
            add_tags=add_tags,
            slot=Planner.ActionSlot.ULTIMATE,
            reason=reason,
            can_execute=can_execute,
            availability=self.ultimate_available,
        )
        if plan is not None:
            plan.add(action)
        return action

    @deprecated("Use self.register_skill(plan, ...)", category=None)
    def click_skill_action(
        self,
        plan: CombatPlan | None = None,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "skill action available",
        can_execute: ActionPredicate | None = None,
    ):
        """创建一个 E 动作声明, 可选择加入指定计划。

        Args:
            plan: 要加入的计划。传入时自动追加动作; 不传时只创建动作声明。
            name: 动作名; 默认 "{角色名}_skill", 用于日志和 planner 精确匹配。
            tags: 替换默认动作标签; 默认为 {Planner.ActionTag.SKILL_ACTION}。
            add_tags: 在最终标签上追加一个或多个标签。
            reason: planner 选择或执行此动作时使用的说明。
            can_execute: 接收 CombatContext 的额外条件; 返回 False 时禁止执行。
                技能本身不可用时, 动作仍会被禁止。

        Returns:
            保留 click_skill 参数提示的动作声明。直接 yield skill 使用已有参数;
            yield skill(down_time=0.3) 为本次释放指定参数。

        Behavior:
            - 创建动作和绑定参数都不会发送输入, 动作交给 planner 后才执行。
            - 自动设置 slot=Planner.ActionSlot.SKILL。
            - 执行前检查 skill_available() 和额外的 can_execute 条件。
            - 切人评分使用 skill_available() 判断技能是否就绪。
            - planner 根据动作槽位和 reservation 检查是否允许释放。
            - 固定执行 self.click_skill(...), 不接受 execute 替换。
            - down_time 默认保持 0.01 秒, 其余释放参数沿用 click_skill() 的默认值。
              释放参数写在返回动作的调用处, 含义见 click_skill()。
            - 如果计划中的动作也应使用指定参数, 先绑定再加入计划。
              plan.add() 只追加动作, 不会替换已经登记的版本。

        Examples:
            skill = self.click_skill_action()
            skill = plan.add(skill(down_time=0.3, post_sleep=0.2))
            yield skill
        """
        action = native_action(
            self.click_skill,
            name=name or f"{self}_skill",
            tags=tags or {Planner.ActionTag.SKILL_ACTION},
            add_tags=add_tags,
            slot=Planner.ActionSlot.SKILL,
            reason=reason,
            can_execute=can_execute,
            availability=self.skill_available,
            down_time=0.01,
        )
        if plan is not None:
            plan.add(action)
        return action

    @deprecated("Use plan.register(self.method, ...)", category=None)
    def planner_action[**P](
        self,
        tags: set[Planner.ActionTag] | Planner.ActionTag,
        execute: Callable[Concatenate[CombatContext, P], ActionResult | bool | None],
        name: str | None = None,
        slot: Planner.ActionSlot | None = None,
        reason: str = "",
        can_execute: ActionPredicate | None = None,
        priority_ready: ActionPredicate | None = None,
        *,
        plan: CombatPlan | None = None,
    ) -> _BoundAction[P]:
        """创建一个交给 CombatPlanner 调度的自定义动作声明。

        Args:
            tags: 动作标签集合; 也可以传入单个 Planner.ActionTag。
            execute: 接收 CombatContext 的执行函数。返回 True 表示成功,
                False、None 或无 return 表示失败; 也可以返回 ActionResult。
                额外参数应提供默认值, 供 planner 提前调度动作时使用。
            name: 高级精确匹配用的动作名和日志名; 普通动作不传时保持空字符串。
            slot: 动作槽位; 需要被 route 或 reservation 匹配时应设置。
            reason: 日志和切人理由。
            can_execute: 接收 CombatContext 的硬限制; 返回 False 时不执行也不评分。
            priority_ready: 接收 CombatContext 的评分条件; 返回 False 时不参与动作评分,
                但不会单独禁止已在场角色执行此动作。
            plan: 要加入的计划。传入时自动追加动作; 不传时只创建动作声明。

        Returns:
            可直接 yield 的动作声明, 保留 execute 中首个 context 之后的参数提示。
            绑定参数后的动作仍可继续调用, 也可交给 plan.add() 登记。

        Behavior:
            - 创建动作和绑定参数都不会调用 execute, 动作交给 planner 后才执行。
            - planner 执行时自动传入 CombatContext, 调用返回动作时不需要传 context。
            - action(...) 为本次执行绑定参数, 不会自动登记或替换计划中的动作。
            - 指定 slot 后, planner 会自动检查 reservation;
              can_execute 只需表达额外机制限制, 不需要重复检查槽位权限。
            - 动作执行时长由 execute 负责, 长动作应自行设置退出条件。
        """

        action = custom_action(
            execute,
            name=name or "",
            tags=set(tags) if isinstance(tags, set) else {tags},
            slot=slot,
            reason=reason,
            can_execute=can_execute,
            priority_ready=priority_ready,
        )
        if plan is not None:
            plan.add(action)
        return action

    def has_cd(self, box_name):
        """检查指定技能是否在冷却中 (代理到 task.has_cd)。

        Args:
            box_name (str): 技能UI区域名称。

        Returns:
            bool: 如果在冷却则返回 True。
        """
        return self.task.has_cd(box_name)

    def is_available(self, percent, box_name):
        """判断技能是否可用 (基于UI百分比和冷却状态)。

        Args:
            percent (float): 技能UI白色像素百分比。
            box_name (str): 技能UI区域名称。

        Returns:
            bool: 如果可用则返回 True。
        """
        return percent == 0 or not self.has_cd(box_name)

    def switch_out(self):
        """角色被切换下场时的状态更新。"""
        self.last_switch_time = self.clock.now()
        self.is_current_char = False
        self.has_intro = False

    def mark_dead(self, reason: str = ""):
        """标记角色已死亡，让 planner 后续调度跳过该角色。"""

        if not self.is_dead:
            self.logger.info(f"mark dead {reason}".strip())
        self.is_dead = True
        self.is_current_char = False
        self.has_intro = False

    def clear_dead(self):
        """清除死亡标记。战斗结束或重新加载队伍时调用。"""

        self.is_dead = False

    def __repr__(self):
        """返回角色类名作为其字符串表示。"""
        return self.__class__.__name__

    def switch_next_char(self, post_action=None, free_intro=False):
        """切换到下一个角色 (代理到 task.switch_next_char)。

        Args:
            post_action (callable, optional): 切换后执行的动作。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技。默认为 False。
        """
        self.has_intro = False
        self._ultimate_available = self.ultimate_available()
        self.task.switch_next_char(self, post_action=post_action, free_intro=free_intro)

    def switch_other_char(self):
        """切换到其他角色 (代理到 task.switch_other_char)。"""

        self.task.switch_other_char(self)

    def sleep(self, sec, checks: SleepChecks = SleepChecks.ALL):
        """Sleep for the requested duration while running the selected checks."""
        if checks == self.SleepChecks.ALL:
            self.task.sleep(sec)
            return

        with self.task.skip_sleep_checks() as skip:
            skip.check_combat = not bool(checks & self.SleepChecks.COMBAT)
            skip.sound_combat_context = not bool(checks & self.SleepChecks.SOUND)
            self.task.sleep(sec)

    def alert_skill_failed(self):
        self.task.log_error(
            "Click skill/ult failed, check if the keybinding is correct in ok-nte settings!",
            notify=True,
        )
        self.task.screenshot("click_skill too long, breaking")

    def _try_available_action(
        self,
        action_type,
        available,
        send_action,
        send_click=True,
        time_out=SKILL_TIME_OUT,
    ) -> _AvailableActionResult:
        start = self.clock.now()
        result = _AvailableActionResult()

        while True:
            status = self._check_available_action_result(
                action_type,
                result,
                start,
                time_out,
                available,
            )
            if status is not _AvailableActionStatus.CONTINUE:
                result.status = status
                return result
            else:
                self.logger.debug(f"{action_type} available click/send")
                if send_click and self.click(action_name=f"{action_type}_click", interval=0.3):
                    self.sleep(0.1)
                sent = send_action()
                if not result.clicked and sent is not False:
                    result.clicked = True
                    result.action_time = self.clock.now()
                    self.sleep(0.1, checks=self.SleepChecks.NONE)
                    continue
            self.sleep(0.1)

    def _check_available_action_result(
        self,
        action_type,
        result: _AvailableActionResult,
        start,
        time_out,
        available,
    ) -> _AvailableActionStatus:
        now = self.clock.now()
        elapsed = now - start

        if elapsed > time_out:
            result.timed_out = True
            return _AvailableActionStatus.TIMEOUT

        if not self.task.is_in_team():
            self.task.in_animation = True
            result.animation_start = result.action_time or now
            return _AvailableActionStatus.ANIMATION
        elif not available():
            self.logger.debug(f"{action_type} not available break")
            return (
                _AvailableActionStatus.RELEASED
                if result.clicked
                else _AvailableActionStatus.UNAVAILABLE
            )
        self.sleep(0.1, checks=self.SleepChecks.NONE)
        return _AvailableActionStatus.CONTINUE

    def click_ultimate(self, send_click: bool = True, wait_if_no_cd: float = 0) -> bool:
        """尝试释放终结技。

        Args:
            send_click (bool, optional): 进入动画后是否发送普通点击。默认为 False。
            wait_if_no_cd (float, optional): 如果技能冷却已完成, 等待多少秒。默认为 0。

        Returns:
            bool: 如果成功释放则返回 True。
        """
        if not self.task.combat_session.use_ultimate:
            return False

        self._ultimate_guard(wait_if_no_cd)

        self.logger.debug("click_ultimate start")
        if not self.task.in_animation:
            result = self._try_available_action(
                "ultimate",
                self.ultimate_available,
                lambda: self.send_ultimate_key(
                    action_name="ultimate_send", interval=0.15, down_time=0.05
                ),
                send_click=send_click,
            )
        else:
            result = _AvailableActionResult(
                clicked=True,
                action_time=self.clock.now(),
                status=_AvailableActionStatus.ANIMATION,
            )

        return self._finish_ultimate_action(result, send_click)

    def _ultimate_guard(self, wait_if_no_cd):
        if self.ultimate_available():
            if self.task.combat_detect_uncertain:
                self.logger.info("click_ultimate blocked by combat_detect_uncertain")
                while self.task.combat_detect_uncertain:
                    self.click_with_interval()
                    self.sleep(0.1)
                self.logger.info("click_ultimate unblocked")
        elif wait_if_no_cd > 0:
            timer = self.clock.timer(timeout=wait_if_no_cd)
            while not self.has_cd("ultimate") and not timer.expired():
                self.sleep(0.1)

    def _finish_ultimate_action(self, result: _AvailableActionResult, send_click):
        if result.timed_out:
            self.alert_skill_failed()
            self.task.raise_not_in_combat("too long clicking a ultimate")

        if not result.clicked:
            return False

        clicked = result.clicked
        start = result.animation_start or self.clock.now()
        ultimate_animation_click = (
            (lambda: self.click(action_name="ultimate_click", interval=0.25))
            if send_click
            else None
        )

        with self.task.skip_sleep_checks() as skip:
            skip.all = True
            if result.status is _AvailableActionStatus.ANIMATION:
                self.logger.debug("clicked ultimate has animation")
                animated = self._wait_action_animation(
                    start=start,
                    on_wait=ultimate_animation_click,
                )
                clicked = clicked or animated
            elif result.status is _AvailableActionStatus.RELEASED:
                self.logger.debug("clicked ultimate no animation")
            duration = self._wait_ultimate_unfreeze(start)

        self._ultimate_available = False
        self.last_ultimate_time = self.clock.now()
        if clicked:
            self.logger.info(f"click_ultimate end {duration}")
        return clicked

    def _wait_ultimate_unfreeze(self, start, click=True):
        self.logger.info("waiting for ultimate unfrozen")
        post_action = (
            (lambda: self.click(action_name="ultimate_click", interval=0.25)) if click else None
        )

        if self.ultimate_freeze_wait is not None:
            wait = self.ultimate_freeze_wait
            if click:
                self.continues_normal_attack(wait)
            else:
                self.sleep(wait)
            duration = self.clock.now() - start
            self.add_freeze_duration(start, duration)
            return duration

        self.task.wait_until(
            lambda: self.has_cd("ultimate"),
            post_action=post_action,
            time_out=1,
        )
        box_ultimate = self.task.get_box_by_name(Labels.box_ultimate)
        snapshot = box_ultimate.crop_frame(self.task.frame)
        processed_snapshot = gf.isolate_text_to_black(snapshot)

        def condition():
            if not self.task.find_one(
                Labels.box_ultimate,
                template=processed_snapshot,
                box=box_ultimate.scale(1.1, 1.1),
                frame_processor=gf.isolate_text_to_black,
                threshold=0.9,
            ):
                self.logger.info("ultimate unfreeze cause cd changed")
                return True
            elif not self.available("ultimate", check_cd=False):
                self.logger.info("ultimate unfreeze cause ultimate not available")
                return True

        self.task.wait_until(
            condition,
            time_out=2,
            post_action=post_action,
        )
        duration = self.clock.now() - start
        self.add_freeze_duration(start, duration)
        return duration

    def click_skill(
        self,
        down_time: float = 0.05,
        post_sleep: float = 0,
        send_click: bool = True,
        time_out: float = 0,
    ) -> bool:
        """尝试释放技能。

        Args:
            down_time (float, optional): 按键按下的持续时间。默认为 0.01。
            post_sleep (float, optional): 释放技能后的休眠时间。默认为 0。
            send_click (bool, optional): 在释放技能前是否发送普通点击。默认为 True。
            time_out (float, optional): 技能释放的超时时间。默认为 0。
        Returns:
            bool: 是否成功点击。
        """
        self.logger.debug("click_skill start")
        the_time_out = SKILL_TIME_OUT if time_out == 0 else time_out
        result = self._try_available_action(
            "skill",
            self.skill_available,
            lambda: self.send_skill_key(
                down_time=down_time,
                action_name="skill_send",
                interval=0.15,
            ),
            send_click=send_click,
            time_out=the_time_out,
        )
        return self._finish_skill_action(result, post_sleep)

    def _finish_skill_action(self, result: _AvailableActionResult, post_sleep=0) -> bool:
        if result.timed_out:
            self.alert_skill_failed()
            self.task.raise_not_in_combat("too long clicking a ultimate")

        clicked = result.clicked
        skill_click_time = result.action_time
        animation_start = result.animation_start
        if animation_start:
            self._wait_action_animation(start=animation_start)
            self.add_freeze_duration(animation_start, self.clock.now() - animation_start)
        if clicked:
            self.last_skill_time = skill_click_time
            with self.task.skip_sleep_checks() as skip:
                skip.all = True
                self.task.wait_until(
                    lambda: not self.skill_available(check_color=False), time_out=1
                )
            self.sleep(post_sleep)
        duration = self.clock.now() - skill_click_time if skill_click_time else 0
        self.logger.debug(
            f"click_skill end clicked {clicked} duration {duration} "
            f"animated {bool(animation_start)}"
        )
        return clicked

    def _wait_action_animation(
        self,
        start,
        timeout=10,
        on_wait=None,
    ):
        animated = False
        timeout_start = start
        try:
            while not self.task.is_in_team():
                self.task.in_animation = True
                animated = True
                if on_wait is not None:
                    on_wait()
                if bool(timeout_start) and self.clock.now() - timeout_start > timeout:
                    self.task.raise_not_in_combat("animation too long")
                self.sleep(0.005, checks=self.SleepChecks.NONE)
        finally:
            self.task.in_animation = False
        return animated

    def click_arc(self):
        return self.send_arc_key()

    def send_skill_key(self, after_sleep=0, interval=-1, down_time=0.01, action_name=None):
        """发送技能按键。

        Args:
            after_sleep (float, optional): 发送后的休眠时间。默认为 0。
            interval (float, optional): 按键按下和释放的间隔。默认为 -1 (使用默认值)。
            down_time (float, optional): 按键按下的持续时间。默认为 0.01。
        """
        self._skill_available = False
        return self.send_key(
            self.get_skill_key(),
            interval=interval,
            down_time=down_time,
            after_sleep=after_sleep,
            action_name=action_name,
        )

    def send_arc_key(self, after_sleep=0, interval=-1, down_time=0.01):
        """发送弧盘技能的按键。

        Args:
            after_sleep (float, optional): 发送后的休眠时间。默认为 0。
            interval (float, optional): 按键按下和释放的间隔。默认为 -1 (使用默认值)。
            down_time (float, optional): 按键按下的持续时间。默认为 0.01。
        """
        return self.send_key(
            self.get_arc_key(), interval=interval, down_time=down_time, after_sleep=after_sleep
        )

    def send_ultimate_key(self, after_sleep=0, interval=-1, down_time=0.01, action_name=None):
        """发送终结技按键。

        Args:
            after_sleep (float, optional): 发送后的休眠时间。默认为 0。
            interval (float, optional): 按键按下和释放的间隔。默认为 -1 (使用默认值)。
            down_time (float, optional): 按键按下的持续时间。默认为 0.01。
        """
        self._ultimate_available = False
        return self.send_key(
            self.get_ultimate_key(),
            interval=interval,
            down_time=down_time,
            after_sleep=after_sleep,
            action_name=action_name,
        )

    def check_combat(self):
        """检查战斗状态 (代理到 task.check_combat)。"""
        self.task.check_combat()

    def sync_check_combat(self):
        """刷新当前帧, 临时同步检测并走完整的战斗检查流程。"""
        policy = self.task.combat_detect_policy
        old_sync = policy.sync
        policy.sync = True
        try:
            self.task.next_frame()
            self.check_combat()
        finally:
            policy.sync = old_sync

    def reset_state(self):
        """重置角色的战斗相关状态 (如入场技标记)。"""
        self.has_intro = False
        self.clear_dead()
        self._ultimate_available = False
        self._skill_available = False

    def on_combat_end(self, chars):
        """当战斗结束时, 角色可能需要执行的特定清理逻辑。

        Args:
            chars (list[BaseChar]): 队伍中所有角色的列表。
        """
        pass

    @property
    def add_freeze_duration(self):
        """添加冻结持续时间 (代理到 task.add_freeze_duration)。"""
        return self.task.add_freeze_duration

    @property
    def time_elapsed_accounting_for_freeze(self):
        """计算扣除冻结时间后经过的时间 (代理到 task.time_elapsed_accounting_for_freeze)。"""
        return self.task.time_elapsed_accounting_for_freeze

    def get_ultimate_key(self):
        """获取终结技按键 (代理到 task.get_ultimate_key)。"""
        return self.task.get_ultimate_key()

    def get_skill_key(self):
        """获取技能按键 (代理到 task.get_skill_key)。"""
        return self.task.get_skill_key()

    def get_arc_key(self):
        """获取弧盘技能按键 (代理到 task.get_arc_key)。"""
        return self.task.get_arc_key()

    def skill_available(self, check_color=True):
        """判断技能是否可用。

        Args:
            check_color (bool, optional): 是否检查技能UI颜色(是否点亮)。默认为 True。

        Returns:
            bool: 如果可用则返回 True。
        """
        return self.available("skill", check_color=check_color)

    def available(self, box, check_color=True, check_cd=True):
        if box == "ultimate" and not self.task.combat_session.use_ultimate:
            return False
        if self.is_current_char:
            return self.task.available(box, check_color=check_color, check_cd=check_cd)
        else:
            if box == "ultimate":
                return self.task.ultimate_available(self.index)
            return not self.task.has_cd(box, self.index)

    def is_cycle_full(self):
        """判断当前环合是否已满 (代理到 task.is_cycle_full)。"""
        return self.task.is_cycle_full()

    def ultimate_available(self, check_color=True):
        """判断终结技是否可用。

        Returns:
            bool: 如果可用则返回 True。
        """
        return self.available("ultimate", check_color=check_color)

    def __str__(self):
        """返回角色类名作为其字符串表示。"""
        return self.__repr__()

    def normal_attack_until_can_switch(self):
        """普通攻击直到可以切人。"""
        self.click()
        while self.time_elapsed_accounting_for_freeze(self.last_perform) < 1.1:
            self.click(interval=0.1)

    def wait_switch_cd(self):
        since_last_switch = self.time_elapsed_accounting_for_freeze(self.last_perform)
        if since_last_switch < 1:
            self.logger.debug(f"wait_switch_cd {since_last_switch}")
            self.continues_normal_attack(1 - since_last_switch)

    def continues_normal_attack(
        self,
        duration: float,
        interval: float = 0.1,
        after_sleep: float = 0,
        until_skill_full: bool = False,
        until_cycle_full: bool = False,
    ):
        """持续进行普通攻击一段时间。

        Args:
            duration (float): 持续时间 (秒)。
            interval (float, optional): 每次攻击的间隔时间。默认为 0.1。
            click_skill_if_ready_and_return (bool, optional): 如果技能可用,
                是否立即释放并返回。默认为 False。
            until_cycle_full (bool, optional): 是否持续攻击直到协奏值满。默认为 False。
        """
        start = self.clock.now()
        while self.clock.now() - start < duration:
            if until_skill_full and self.skill_available():
                return
            if until_cycle_full and self.is_cycle_full():
                return
            self.click()
            self.sleep(interval)
        self.sleep(after_sleep)

    def continues_click(self, key, duration, interval=0.1):
        """持续发送指定按键一段时间。

        Args:
            key (str): 要发送的按键。
            duration (float): 持续时间 (秒)。
            interval (float, optional): 每次发送按键的间隔。默认为 0.1。
        """
        start = self.clock.now()
        while self.clock.now() - start < duration:
            self.send_key(key, interval=interval)

    def continues_right_click(self, duration, interval=0.1, direction_key=None):
        """持续进行鼠标右键点击操作一段时间，可选同时按住方向键。

        Args:
            duration (float): 持续时间 (秒)。
            interval (float, optional): 每次发送按键的间隔。默认为 0.1。
            direction_key (str, optional): 如果指定，则在点击期间同时按下此键
                （如 'w'、'a'、's'、'd'）。
        """
        try:
            if direction_key is not None:
                self.task.send_key_down(direction_key)
                self.sleep(0.1)
            start = self.clock.now()
            while self.clock.now() - start < duration:
                self.click(interval=interval, key="right")
        finally:
            if direction_key is not None:
                self.task.send_key_up(direction_key)

    def normal_attack(self):
        """执行一次普通攻击。"""
        self.logger.debug("normal attack")
        self.check_combat()
        self.click()

    def heavy_attack(self, duration=0.6):
        """执行一次重攻击。

        Args:
            duration (float, optional): 重攻击按键按下的持续时间。默认为 0.6。
        """
        self.check_combat()
        self.logger.debug("heavy attack start")
        try:
            self.task.mouse_down()
            self.sleep(duration)
        finally:
            self.task.mouse_up()
        self.sleep(0.01)
        self.logger.debug("heavy attack end")

    def current_skill(self):
        """获取当前技能UI白色像素百分比。"""
        return self.task.calculate_color_percentage(
            text_white_color, self.task.get_box_by_name("box_skill")
        )

    def current_ultimate(self):
        """获取当前终结技UI白色像素百分比。"""
        return self.task.calculate_color_percentage(
            text_white_color, self.task.get_box_by_name("box_ultimate")
        )

    def check_outro(self):
        """协奏入场时判断延奏来源

        Returns:
            string:非协奏入场返回'null'，否则范围角色名如'char_sanhua'
        """
        if not self.has_intro:
            return "null"
        time = self.clock.UNSET
        outro = "null"
        for char in self.task.chars:
            if char == self:
                continue
            elif char.last_switch_time > time:
                time = char.last_switch_time
                outro = char.char_name
        self.logger.info(f"erned outro from {outro}")
        return outro

    def is_first_engage(self):
        """判断角色是否为本场第一个实际执行战斗逻辑的角色。"""
        return self.task.is_first_engage(self)

    def consume_first_engage(self):
        """消费本场首次登场标记, 同一场战斗仅会成功一次。"""
        return self.task.consume_first_engage(self)

    def now(self) -> clock.TimePoint:
        """Compatibility alias for external characters; built-ins use self.clock."""
        return self.clock.now()

    def get_teammate_by_class(self, *char_classes):
        """按角色类型查找队友, 并按参数顺序返回匹配结果。

        每个类型对应一个结果, 未找到时为 None。当前角色不会作为自己的队友返回。
        """
        teammates = [c for c in self.task.chars if c is not None and c.index != self.index]

        return tuple(
            next((c for c in teammates if isinstance(c, cls)), None) for cls in char_classes
        )

    def get_teammates_by_role(self, role: Planner.Role) -> list["BaseChar"]:
        """按队伍定位查找所有队友, 返回匹配角色组成的列表。

        当前角色不会出现在结果中; 队伍中没有匹配角色时返回空列表。
        """
        return [
            char
            for char in self.task.chars
            if char is not None and char.index != self.index and char.describe_role().role == role
        ]

    def find_element_reaction_target(self, char=None):
        """查找可与指定角色触发环合反应的队友。

        未传入角色时以当前角色为起点。没有可触发反应的队友时返回 None。
        """
        if char is None:
            char = self
        return self.task.find_element_reaction_target(char)

    @legacy_context_hook
    def should_instant_cycle(self):
        context = self._combat_context
        return self.skill_available() and (
            not self.has_element_reaction_teammate()
            or not self.is_cycle_full()
            or (
                context is not None
                and context.strict_route_wants_action(self, slot=Planner.ActionSlot.SKILL)
            )
        )

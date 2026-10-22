# Combat Planner 开发指南

> **提示**：角色的具体代码实现可在 [`src/char`](../../../src/char) 目录中找到，也可查看
> [GitHub](https://github.com/BnanZ0/ok-nte/tree/main/src/char) 或
> [CNB](https://cnb.cool/BnanZ0/ok-nte-update/-/tree/main/src/char) 上的代码目录。

Planner 是队伍大脑。角色只声明一个 `CombatPlan`：

- `actions`：planner 可见的动作目录，用于切人评分、route/request/reservation 匹配。
- `claims`：`FieldClaim` 入场诉求，用于表达“我现在应该被切进来”。
- `entry`：普通入场后的 Python generator 动作流。未提供时默认按 `actions` 顺序执行。

角色代码从 `src.combat.planner` 导入公开类型。以下是常用类型示例，按需增删：

```python
from src.combat.planner import (
    ActionIntent,
    ActionReservation,
    ActionResult,
    ActionSlot,
    ActionTag,
    CombatContext,
    ExpectedEntry,
    FieldClaim,
    FollowupStep,
    Planner,
    RequestHandle,
    RoleProfile,
    SwitchInGuard,
)
```

`src.combat.planner` 只导出正式开发 API。角色代码不要直接导入
`planner/core.py`、`planner/requests.py`、`planner/state.py` 等内部模块。
本文聚焦角色作者 API；切人执行生命周期由 `BaseCombatTask` 管理，角色代码不需要直接
操作 `CombatPlanner` 的执行端方法。

## 快速入口

普通角色通常只需要覆盖 `describe_role()` 和 `combat_plan(context)`：

```python
def describe_role(self):
    return RoleProfile(
        role=Planner.Role.SUB_DPS,
        max_field_time=1.5,
    )

def combat_plan(self, context: CombatContext):
    return self.plan(
        self.click_ultimate_action(),
        self.click_skill_action(),
    )
```

复杂动作顺序用同一个 plan 里的 action 变量写 entry flow。一个 action 在一次 entry 中只能
执行一次；有限次的额外执行使用 `repeat_for_entry()`：

```python
def combat_plan(self, context: CombatContext):
    plan = self.plan()
    skill = self.click_skill_action(plan, reason="skill available")
    ultimate = self.click_ultimate_action(plan, reason="ultimate available")

    def entry():
        skill_result = yield skill
        if skill_result and self.ultimate_available():
            self.sleep(0.6)

        ultimate_result = yield ultimate
        if ultimate_result:
            yield skill.repeat_for_entry()

    plan.entry = entry
    return plan
```

`yield action` 会执行该 action，并把 `ActionResult` 送回 generator。只有动作成功时
`bool(ActionResult)` 才为 True，所以可以直接写：

```python
a = yield action_a
b = yield action_b

if a and b:
    yield action_c

if a and not b:
    yield fallback_action
```

## CombatPlan

用 `self.plan()` 创建空计划, 再传给角色的 Q/E helper。helper 会登记动作, 并返回保留
原始执行方法参数名与类型的可调用声明。Planner 只接收普通 `ActionIntent`, 释放参数
及其绑定逻辑均留在 `BaseChar`。

```python
def combat_plan(self, context):
    plan = self.plan()
    skill = self.click_skill_action(plan)
    ultimate = self.click_ultimate_action(plan)
    if self.should_claim_field():
        plan.add(FieldClaim.high(reason="burst window"))

    def entry():
        if (yield skill(down_time=0.3)):
            yield ultimate(send_click=False)

    plan.entry = entry
    return plan
```

`plan.add(...)` 返回原对象, `None` 会被忽略。动作按登记顺序加入目录, 未设置 `entry`
时也按此顺序尝试。仅在构建计划时登记动作和 claim; `skill(...)` 只绑定本次执行,
不会发送输入或登记新动作。
原有 `self.plan(*actions, claims=None, entry=None)` 写法继续支持: 不传 plan 的 helper
仍返回可加入该目录的可调用声明。使用已有参数时直接 `yield action`,
只有需要指定本次释放参数时才写 `yield action(...)`, 不需要空的 `()`。

规则：

- 创建 plan 时只声明动作和入场诉求，不要发送输入。
- 不要在创建 plan 时发布一次性协作请求；`request_route()`、`request_switch()`、
  `request_role()`、`request_tags()` 和临时 `reserve_actions()` 应在 action 执行期间发布，
  或在 entry flow 收到成功 result 后发布。队伍级长期策略放在 `combat_policies()`。
- entry flow 发布的请求会在下一次 `yield` 或流程结束时收集；收到 result 后发布请求并直接
  `return` 也会生效。
- `actions` 是评分和协作匹配目录；`entry` 是普通入场执行流程。
- `claims` 可以传多个独立入场理由；它们不会叠加分数，planner 只取当前匹配角色的最高优先级 claim。
- strict route、expected entry、active request 的硬调度优先于普通 entry flow。
- 普通 entry flow 受 planner 的单次入场动作数上限约束。
- 同一个 action 在同一次入场中只会真实执行一次。

### 释放参数

释放参数写在返回动作的调用处, 不写在 `click_skill_action(...)` 或
`click_ultimate_action(...)` 上。参数提示来自 `click_skill`/`click_ultimate`, 由
`BaseChar` 中一个通用包装保留。原方法增加参数时不必修改 planner 类型或再维护一份
Q/E 参数签名。

```python
if (yield skill(down_time=0.3, post_sleep=0)):
    yield ultimate(send_click=False)
yield skill(down_time=0.01).repeat_for_entry()
```

这些调用只创建普通动作副本, 将参数捕获在本次 `execute` 闭包中。动作身份、标签、slot、
评分就绪条件、冷却检查和权限保持不变。改变参数不会允许同次入场再次执行,
需要额外释放时对绑定后的动作显式调用 `repeat_for_entry()`。
未提供的原方法参数沿用其默认值, E 仍保留原 helper 的 `down_time=0.01` 默认配置。
参数名错误会在绑定时拒绝, 不会发送输入。创建计划和绑定动作均不执行释放函数。

Q/E helper 固定绑定原生的 `click_skill`/`click_ultimate`, 不接受 `execute` 替换。
自定义执行函数只通过 `planner_action(...)` 声明。需要根据状态调整释放参数时,
直接在动作调用处计算并传入。

如果普通执行和严格路线都应使用同一套指定参数, 先绑定再登记：

```python
skill = self.click_skill_action()  # 只创建, 不自动登记。
skill = plan.add(skill(down_time=self.skill_down_time, post_sleep=0.2))
# entry 内:
yield skill
yield skill(down_time=0.1, post_sleep=0.2).repeat_for_entry()
```

绑定参数后的动作仍保留可调用类型和原方法的参数提示, 可以继续使用同一个变量。
再次传参时会按原方法和 helper 的默认值重新绑定, 不会合并上一份动作的参数配置。

目录调度使用已绑定的参数值。参数在绑定时捕获, 不会在执行时重新计算。entry 可通过
`skill(...)` 绑定另一次调用; 如果严格路线已经执行同一动作, entry 会收到先前结果,
不会再次释放。`plan.add()` 仍只追加: 配置单个目录动作时, 不要先登记默认版本,
再追加同身份的参数版本。

### 自定义动作

同一套包装也保留自定义执行函数首个 `CombatContext` 之后的参数签名：

```python
def perform_burst(self, context: CombatContext, *, duration: float = 0.5) -> bool:
    self.continues_normal_attack(duration)
    return True

# combat_plan 内:
burst = self.planner_action(
    plan=plan,
    tags=Planner.ActionTag.DAMAGE,
    execute=self.perform_burst,
)
# entry 内:
yield burst(duration=0.8)
```

自定义动作具有相同的参数提示、绑定、权限、结果和重复执行规则。目录默认执行只传入
`context`, 所以执行函数的额外参数应有默认值。需要动态默认行为时在执行函数内读取
最新状态; 函数类型的自定义参数作为普通值传入, planner 不解析它们。

## ActionIntent

`ActionIntent` 表达“角色进场后可以尝试做什么”。不要把一次普攻、等待、连点等
内部细节拆成很多 action；这些应写在一个 action 的 `execute` 内。

BaseChar 的 helper 返回兼容 `ActionIntent` 的可调用动作声明。调用后得到绑定参数的
普通 `ActionIntent`, planner API 不再维护 Q/E 专用参数类。

字段：

- `tags: set[ActionTag]`：动作意义和评分依据。
- `execute: Callable[[CombatContext], ActionResult | bool | None]`：真正执行动作。
- `name: str = ""`：高级精确匹配和日志名。
- `slot: ActionSlot | None = None`：动作槽位。协作路线和 reservation 优先用 slot 匹配。
- `reason: str = ""`：planner 日志和切人理由。
- `can_execute: Callable[[CombatContext], bool] | None`：planner 层硬限制。
- `priority_ready: Callable[[CombatContext], bool] | None`：只用于切人评分。

`action.repeat_for_entry()` 允许同一次 entry 再次 `yield` 该动作，同时保留原 action 的
执行内容、slot、标签和 `can_execute` 限制。它适合 `Q -> E -> 再尝试一次 E` 这类有限
entry flow；返回的副本通常只在 entry flow 中 yield，不应加入 `CombatPlan.actions`。

每次 `yield` 都会计入单次 entry 的动作上限。不要在需要持续运行的长时间循环中 yield
它；此类循环应在调用已有动作 helper 前，先通过
`context.is_action_allowed(self, action)` 检查完整 action 权限。这样循环保持由角色代码
控制，同时仍遵守 planner 的 `can_execute` 和 reservation 规则。

如果 action 设置了 `slot`，planner 会自动通过 `context.is_slot_available(...)`
检查 reservation。开发者传入的 `can_execute` 只需要表达额外机制限制。需要在 entry
flow 外预查询完整 action 时，使用 `context.is_action_allowed(self, action)`；它同时检查
`can_execute` 和 slot reservation。普通或有限 entry 动作仍直接 `yield action`。

`execute` 返回规则：

- 返回 `True`：成功。
- 返回 `False` / `None` / 没写 `return`：失败。
- 返回 `ActionResult`：使用 `ActionResult.success`。
- 返回 `1`、`"ok"` 这类 truthy 值不会被当成成功。

普通角色不需要手写 `ActionResult`。只有需要自定义 result name/tags/slot/reason
时才手写：

```python
def execute(context):
    return ActionResult(
        success=True,
        name="custom support action",
        tags={ActionTag.SUPPORT},
        slot=ActionSlot.CUSTOM,
        reason="healing completed",
    )
```

`ActionResult` 的 `tags` 和 `slot` 也用于匹配协作请求；普通动作应优先让 helper 自动生成结果。

## ActionTag

`ActionTag` 表达动作意义和评分，不能表达某个角色专属机制。

常用标签：

- `ULTIMATE_ACTION`：Q。
- `SKILL_ACTION`：E。
- `ARC_ACTION`：弧盘动作，评分为 0。
- `DAMAGE`：通用伤害动作。
- `SUPPORT`：辅助/治疗/增益类动作。
- `TEAM_BUFF`：为全队提供增益的关键动作。仅在该增益应优先于主 DPS 终结技施放时使用。
- `HIGH_PRIORITY`：显著提高动作的切人评分。用于少数特别值得优先尝试的动作；它不会改变角色上场后的动作执行顺序。
- `FIELD_TIME`：planner 内建站场动作，角色不应自己声明。
- `LEGACY_COMBO`：旧出招表动作。
- `DEFAULT_ACTION`：低价值兜底入口。

切人评分不会累加同一角色所有 action；planner 只挑该角色当前最高分的 ready
action 代表该角色参赛。tag 不控制普通入场流程；普通入场由 `CombatPlan.entry`
控制。

## ActionSlot

`ActionSlot` 是协作匹配用的动作槽位，比 action name 更推荐。

常用槽位：

- `SKILL`：E。
- `ULTIMATE`：Q。
- `ARC`：弧盘。
- `ENTRY_REACTION`：入场/环合反应，不是按键 action。
- `FIELD_TIME`：planner 内建站场。
- `LEGACY_COMBO`：旧出招表。
- `CUSTOM`：特殊动作。

协作和保留尽量写：

```python
FollowupStep.for_action(zero, ActionSlot.SKILL)
ActionReservation.for_action(nanally, ActionSlot.SKILL)
ActionReservation.for_slots(nanally, {ActionSlot.SKILL, ActionSlot.ULTIMATE})
context.is_slot_available(self, ActionSlot.SKILL)
```

## RoleProfile

`describe_role()` 返回 `RoleProfile`，描述队伍定位和普通切人时的站场偏好。`role` 与
`field_preference` 是两个独立维度：前者供 `request_role()` 匹配，后者参与普通切人评分；
它们都不是强制调度指令。

| 字段 | 可选值 / 用途 |
|---|---|
| `role` | `Planner.Role.MAIN_DPS`、`SUB_DPS`、`SUPPORT`；描述队伍定位。默认 `SUB_DPS`。 |
| `field_preference` | `Planner.FieldPreference.MAIN_DPS`、`SUB_DPS`、`SUPPORT`、`SETUP_ONLY`；表达普通情况下的站场倾向。未指定时在构造阶段根据 `role` 推导同名偏好；显式配置优先。 |
| `max_field_time` | 大于 0 时允许 planner 使用内建站场兜底动作，并以此作为最长站场时长；设为 0 可关闭该兜底。默认 1.5 秒。 |
| `combat_start_priority` | 仅用于开战首切；大于 0 才成为首切候选，数值越高越优先。不会影响普通战斗中的切人评分。默认 0。 |

`MAIN_DPS` 倾向持续站场，`SUB_DPS` 倾向短时站场，`SUPPORT` 与 `SETUP_ONLY` 更适合
完成辅助或准备动作后轮换。协作请求和入场诉求会影响最终选择。

普通角色只需声明定位, `MAIN_DPS`、`SUB_DPS`、`SUPPORT` 会分别补全同名站场偏好。
特殊偏好仍可单独指定：

```python
RoleProfile(role=Planner.Role.MAIN_DPS)
RoleProfile(
    role=Planner.Role.SUB_DPS,
    field_preference=Planner.FieldPreference.SETUP_ONLY,
)
```

默认值只在构造时补全, 后续修改 `role` 不会自动改变 `field_preference`。

## BaseChar Helper

### switch_in_guard

目标角色可覆盖 `switch_in_guard()`，延迟普通切人，直到自身满足入场条件：

```python
def switch_in_guard(self, context, from_char, has_intro):
    return SwitchInGuard.delay_until_ready(
        condition=self.entry_state_ready,
        timeout=1.5,
        reason="waiting for entry state",
    )
```

`condition` 返回 True 时继续切人；超时后也会继续切人，因此这是有上限的延迟，不是切人否决。
`poll_interval` 控制检查间隔，`while_waiting` 可提供等待期间的动作回调。默认 guard 立即
允许切入。strict route 和 strict field claim 会跳过这个等待及普通切人冷却；需要 guard
保护的入场不应依赖 strict 调度。

### 开战会话与首次登场

`BaseCombatTask.begin_combat_session()` 是战斗正式开始时的统一入口。它会创建公开的
`task.combat_session`, 调用首切决策并记录实际首发角色; `CombatPlanner` 只负责决定首切
目标, 不执行输入或管理会话。`CombatSession.combat_start` 是本场战斗进入时刻;
`use_ultimate` 与 `switch_enabled` 是本场固定的战斗策略。

`BaseChar.perform()` 开始时会记录本场第一个实际执行战斗逻辑的角色。角色逻辑可用：

```python
if self.is_first_engage():
    # 本场首次实际登场的角色
    ...

if self.consume_first_engage():
    # 全场仅成功一次
    ...
```

`is_first_engage()` 在本场战斗内稳定; `consume_first_engage()` 全场仅返回一次 `True`。
两者都不依赖首切耗时或时间窗口。`task.combat_session` 在首次读取时会创建默认会话;
任务若需要禁止首切和后续切人, 应在调用 `begin_combat_session()` 前设置
`task.combat_session.switch_enabled = False`, 不要在运行期替换切人方法。

### click_ultimate_action

```python
ultimate = self.click_ultimate_action(
    plan,
    name=None,
    tags=None,
    add_tags=None,
    reason="ultimate action available",
    can_execute=None,
)
# entry 内:
yield ultimate(send_click=False, wait_if_no_cd=0.5)
```

- 登记 `slot=ActionSlot.ULTIMATE`, 默认标签 `ULTIMATE_ACTION`, 默认名称 `"{角色名}_ultimate"`。
- `tags` 替换基础标签; `add_tags` 接收单个或多个标签, 追加到最终标签中。
- 执行条件包含 `ultimate_available()` 及额外 `can_execute`; 评分就绪条件使用 `ultimate_available()`。
- 执行固定绑定 `click_ultimate`, 在 `ultimate(...)` 上传入释放参数; 目录调度也需要该参数时, 先绑定再登记。
- 不传 `plan` 时, 可继续通过原有 `self.plan(...)` 写法返回动作目录。

### click_skill_action

```python
skill = self.click_skill_action(
    plan,
    name=None,
    tags=None,
    add_tags=None,
    reason="skill action available",
    can_execute=None,
)
# entry 内:
yield skill(down_time=0.3, post_sleep=0.2)
```

- 登记 `slot=ActionSlot.SKILL`, 默认标签 `SKILL_ACTION`, 默认名称 `"{角色名}_skill"`。
- 标签配置与 Q 相同; 执行条件包含 `skill_available()` 及额外 `can_execute`, 评分就绪条件使用 `skill_available()`。
- 执行固定绑定 `click_skill`, 在 `skill(...)` 上传入释放参数; E 继续保留原 helper 的默认 `down_time=0.01`。

### click_arc_action

```python
self.click_arc_action(
    name=None,
    tags=None,
    add_tags=None,
    reason="arc action available",
    can_execute=None,
    priority_ready=None,
)
```

该 helper 自动设置 `slot=ActionSlot.ARC`，默认使用 `ActionTag.ARC_ACTION`。默认
`priority_ready` 为 False，因此弧盘动作不会单独促使角色切入；角色已在场时仍可按普通
entry 流程尝试。需要时可传 `priority_ready` 改变其评分就绪条件。

### planner_action

```python
self.planner_action(
    tags={ActionTag.SKILL_ACTION},
    execute=self.some_action,
    name=None,
    slot=None,
    reason="",
    can_execute=None,
    priority_ready=None,
)
```

用于创建自定义 action。长动作应在 `execute` 内完成。

## FieldClaim

`FieldClaim` 表达“我应该被切进来”，不是动作。`low`、`normal`、`high` 和
`critical` 抬高普通入场评分；`strict` 在下一次切人决策时直接选定该角色。
角色切入后仍由 planner 从 `actions`、strict route/request 或 `entry` 中选择动作。

普通 claim 可通过 `ExpectedEntry(slot=...)` 指定回场后优先尝试的槽位；需要高级精确匹配时
可设置 `action_name`，或用 `ExpectedEntry.from_action(action)` 从动作声明生成。strict claim
只要求切入，不接受 `expected_entry`。

```python
def combat_plan(self, context):
    claims = []
    if self.has_burst_window():
        claims.append(
            FieldClaim.high(
                reason="burst window active",
                expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
            )
        )
    return self.plan(self.click_ultimate_action(), claims=claims)
```

需要在限时窗口内回场时，可在 `combat_plan()` 中声明 strict claim：

```python
def combat_plan(self, context):
    ultimate = self.click_ultimate_action()
    claims = []
    if self.should_return_now():
        claims.append(
            FieldClaim.strict(
                reason="ultimate window ending",
            )
        )
    return self.plan(ultimate, claims=claims)
```

planner 每次切人决策都会重新读取候选角色的 claim。已锁定的 strict route 优先；
之后 strict claim 优先于环合反应、active request 和普通评分。多个角色同时声明
strict claim 时，planner 用它们的普通评分及最近行动时间决定目标。strict claim
只在当前角色的动作结束后生效，不会中断动作；它只要求切入，不设置 `expected_entry`。
切入后角色按自己的普通 `entry` 流程执行动作。

使用建议：

- 只是 Q/E 可用，不需要 FieldClaim；action 本身会参与评分。
- 需要“之后抢回场”时用普通 FieldClaim；必须在下一次切人决策中回场时用 `FieldClaim.strict()`。
- `FieldClaim.critical()` 仍是普通评分档位，不会强制切人。
- 普通 claim 抢回场后需要优先做某动作时, 加 `expected_entry`。
- 多个 FieldClaim 适合表达多个独立机制入口；planner 不累加 claim 分，只选择最高等级的匹配 claim。

## combat_policies

`combat_policies(context)` 用于随队伍生命周期长期生效的策略。planner reset 当前队伍
时会调用。适合发布常驻 reservation，不适合发布“本次 Q/E 成功后才出现”的临时窗口。

```python
def combat_policies(self, context: CombatContext):
    context.reserve_actions(
        [ActionReservation.for_action(zero, ActionSlot.SKILL)],
        reason="reserve Zero skill",
        until=Planner.NEVER_EXPIRES,
    )
```

## 协作请求

协作请求必须在 action 执行成功后发布，或者在 `combat_policies()` 里发布长期策略。

```python
def combat_plan(self, context):
    setup = self.click_skill_action()

    def entry():
        setup_result = yield setup
        if setup_result:
            context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="setup route",
            )

    return self.plan(setup, entry=entry)
```

常用 API：

- `context.request_route(...)`：固定顺序协作路线。

`FollowupStep.for_switch(target, wait_for_turn=True)` 默认切入后等待目标正常执行完本轮:

```python
context.request_route([
    FollowupStep.for_switch(a),
    FollowupStep.for_action(b, ActionSlot.ULTIMATE),
])
```

`FollowupStep.for_action(target, slot, ...)` 创建指定角色的动作步骤。`optional=True` 时，
目标到场后若没有匹配且可执行的动作，planner 会跳过该步骤；默认的必需步骤会继续等待。
可用 `required_tags` 或 `action_names` 添加匹配条件；普通协作优先使用 slot，动作名适合
需要精确匹配的高级场景。标签条件要求动作至少命中一个指定 tag。

若 route 要求目标切入时触发入场/环合反应，可使用：

```python
FollowupStep.for_entry_reaction(target, reason="trigger entry reaction")
```

这里 A 按自己的正常 entry flow 执行完本轮后, 才推进到 B 的终结技。
A 已在场时也会执行本轮, 不直接跳过。它不指定首动, 不要求入场反应,
也不绕过动作许可或 reservation。本轮结束沿用正常流程的结束条件和动作数上限;
无动作或全部失败时仍可尝试正常站场回退, 不要求某个技能成功才完成步骤。
异常中断不会算作完成; route 过期或被替换后不会再推进旧步骤。

若只要求切入, 使用 `FollowupStep.for_switch(a, wait_for_turn=False)`。
该模式切入成功或目标已在场时立即完成步骤。单步 route 结束后恢复正常流程;
多步 route 会立即推进, **不等待 A 正常流程结束, 也不保证 A 执行任何动作**。

两种模式均继承 strict route 的调度优先级和生命周期。新 route 会替换已有 route,
因此它不是单纯的高优先级 `request_switch()`; 普通独立切人诉求仍使用后者。

- `context.request_switch(...)`：请求下一次普通调度切给某角色。
- `context.request_role(...)`：请求下一次普通调度切给某个队伍定位的角色；多个
  匹配角色时按普通切人评分选择。它不指定动作，也不打断当前 entry flow。
- `context.reserve_actions(...)`：保留队友动作。
- `context.request_tags(...)`：请求不同队友各完成一次带指定 tag 的成功动作；任一指定 tag 匹配即可，每个角色最多计一次。可用 `count` 指定人数，默认不让请求发起者自己满足请求。
  设置 `avoid_source=False` 可允许发起者自己满足请求。

`request_role(Planner.Role.SUPPORT)` 请求的是角色的静态队伍定位；
`request_tags({Planner.ActionTag.SUPPORT})` 请求的是任意支援类动作。前者适合
“让任一辅助角色进场”，后者适合“让任一队友完成一次治疗/增益动作”。
`request_route()` 和 `request_tags()` 可用 `return_to_source=True` 在请求完成后提高发起者
重新入场的优先级。

```python
context.request_role(Planner.Role.SUPPORT, reason="need a support role")
```

这些请求通常返回 `RequestHandle`。可用它查询是否完成或过期，也可用生命周期条件组合
请求。例如，让 reservation 在 route 完成后释放：

```python
route = context.request_route(steps, until=self.window_expired)
if route is not None:
    route.on_fulfilled(self.on_route_complete)
    route.on_expired(self.on_route_expired)
    context.reserve_actions(reservations, until=route.when.fulfilled)
```

`handle.when.fulfilled`、`expired`、`closed` 和 `any` 都是可传给另一个请求 `until=` 的条件；
`is_pending`、`is_fulfilled`、`is_expired`、`is_closed` 可直接查询状态。`handle.status` 为
`Planner.RequestStatus.PENDING`、`FULFILLED` 或 `EXPIRED`；`closed` 是独立状态。使用
`on_finish()` 可响应第一个完成或过期信号；需要区分结果时使用 `on_fulfilled()` 和
`on_expired()`。`request_route()`、`request_switch()`、`request_role()` 和 `request_tags()` 的
`until` 默认为 None，即不会因窗口条件自动过期；
`reserve_actions()` 必须指定 `until`，可传 callable，或传 `Planner.NEVER_EXPIRES` 让它持续到
planner reset。route 步骤完成和 route 窗口过期是不同信号。
`until=Planner.NEVER_EXPIRES` 的 reservation 不会自动结束，也不能配置 `on_finish`。

`CombatContext` 还提供 `has_active_request()`、`has_strict_route()` 和
`strict_route_wants_action(char, slot=..., action_name=..., tags=...)`，供确实需要根据当前协作
状态分支的高级角色逻辑查询。纯 reservation 和纯切人请求不算 active action request。

## 行为摘要

- 切人评分与普通 entry 执行分离。
- 评分使用 `actions` 中最高分 ready action，再叠加 `FieldClaim`、request 和站场偏好分。
- 当前角色普通入场执行由 `entry` 控制；未写 entry 时按 `actions` 顺序执行。
- `priority_ready=False` 只降低切人吸引力，不是硬阻止。
- `can_execute=False` 是硬阻止；被阻止的 entry action 会得到失败 result，不会真实执行。
- strict route、expected entry、active request 优先于普通 entry flow。
- `ActionResult.tags` 不控制 entry flow。


## 运行期计时

角色从 `BaseChar` 继承 `self.clock`, 无需额外 import 即可调用 `self.clock.now()`。
返回值 `TimePoint` 是 `float` 子类, 支持与数字比较、普通算术和格式化。
角色状态、冷却、冻结记录和 planner 使用同一单调时钟来源; 持续时间以秒为单位。
`self.now()` 保留为外部角色代码的兼容入口。冻结 helper 和 planner 计时参数也接受
普通数值型单调时间。新代码优先使用 `clock.UNSET` 或 `None` 表示未发生的事件,
冻结 helper 同时兼容旧数字哨兵。详见[运行期时钟约定](../../runtime_clock.md)。

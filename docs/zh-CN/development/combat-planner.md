# Combat Planner 开发指南

角色负责描述自己的动作与出招流程, Planner 负责选择切入目标和执行协作路线。
内置角色示例见 [src/char](https://github.com/BnanZ0/ok-nte/tree/main/src/char)。
角色只从 `src.combat.planner` 导入公开 API, 不操作 Planner 内部状态。

## 最简单的角色

```python
from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class ExampleChar(BaseChar):
    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS, max_field_time=1.5)

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(plan)
        return plan
```

效果: 普通入场按注册顺序尝试 Q、E。没有成功的已注册动作且协作允许时, Planner 可用普攻站场兜底。
`max_field_time=0` 关闭该兜底; 它不限制角色自己编写的爆发循环时长。
切人评分取角色当前最高分的可用动作, 不累加全部动作, 也不决定入场后的动作顺序。

`RoleProfile` 的常用设置:

| 字段 | 效果 |
| --- | --- |
| `role` | 队伍定位: `Planner.Role.MAIN_DPS`、`SUB_DPS` 或 `SUPPORT`, 默认 `SUB_DPS`。 |
| `field_preference` | 普通切人时的站场倾向。默认根据 role 推导; 可显式指定 `Planner.FieldPreference.SETUP_ONLY` 等偏好。 |
| `max_field_time` | 内建普攻兜底的站场预算, 默认 1.5 秒。 |
| `combat_start_priority` | 大于 0 时参与开战首切, 数值高者优先; 不影响普通切人评分。 |

## 自己控制出招顺序和参数

设置 `plan.entry` 后, 普通入场执行这个无参方法。直接调用原角色方法, 保留原生参数提示和返回值:

```python
def combat_plan(self):
    plan = self.plan()
    self.register_skill(plan)
    self.register_ultimate(plan)
    plan.entry = self.perform_entry
    return plan


def perform_entry(self):
    if self.click_skill(down_time=0.3, post_sleep=0.2):
        self.click_ultimate(send_click=False)
    if self.skill_available():
        self.click_skill()
```

每次调用都是新的尝试, 会重新检查注册的执行条件和 Reserve; Q/E helper 还提供冷却检查。
没有入场去重或动作次数上限,
也不会回放路线此前执行过的结果。普通 E 使用原方法的默认按键时长 0.05 秒;
角色可覆盖自己的默认参数。

普通函数按 Python 语义运行: 请求不会强制让 entry 返回, 后续注册动作仍受 Planner 限制。
即使后续动作被阻止并返回 False, 普通赋值、等待和辅助方法也不会因此自动停止。
希望提前结束时自行写 `return`。

## 注册动作

`register_skill(plan)`、`register_ultimate(plan)` 和 `register_arc(plan)` 分别登记
`click_skill`、`click_ultimate`、`click_arc`, 返回值都是 `None`。
Q/E helper 提供槽位、默认标签和冷却检查; Support 的 Q/E 还会带 `SUPPORT` 标签。
`register_arc()` 默认不因弧盘主动切人。是否由 Planner 接管弧盘仍由角色显式设置
`self.planner_handles_arc = True`, 注册不会修改这个开关; 未接管时默认入口仍可能额外释放一次弧盘。

自定义或复合动作使用普通方法:

```python
def combat_plan(self):
    plan = self.plan()
    plan.register(
        self.perform_burst,
        slot=Planner.ActionSlot.CUSTOM,
        tags={Planner.ActionTag.DAMAGE},
        reason="burst available",
    )
    plan.entry = self.perform_entry
    return plan


def perform_entry(self):
    self.perform_burst(duration=0.8)


def perform_burst(self, duration: float = 0.5) -> bool:
    self.continues_normal_attack(duration)
    return True
```

注册选项同样可传给 Q/E/弧盘 helper, 但 helper 的 slot 已由其动作种类确定:

| 选项 | 用途 |
| --- | --- |
| `name` / `reason` | 动作名称、日志和切人理由; 通常无需指定 name。 |
| `tags` / `add_tags` | tags 替换默认标签, add_tags 追加标签。标签用于评分和协作筛选。 |
| `can_execute` | 无参硬条件, 返回 False 就不执行。通过 `self.combat_context` 查询协作状态。 |
| `priority_ready` | 无参评分条件, 返回 False 只取消该动作的切人评分资格, 不禁止执行。 |
| `slot` | `plan.register()` 的协作槽位, 例如 `Planner.ActionSlot.SKILL`。 |

`plan.register()` 不根据方法名推断 slot、tags 或冷却条件。常用标签有 `SKILL_ACTION`、
`ULTIMATE_ACTION`、`SUPPORT`、`TEAM_BUFF`、`HIGH_PRIORITY` 和 `DAMAGE`。
`SKILL_ACTION`、`ULTIMATE_ACTION` 已有技能输出评分, 不必额外叠加 DAMAGE。

同一计划内每个方法只注册一次, 同一 slot 可以注册多个不同方法。
例如完整 E 连招和单次 E 都可属于 SKILL; slot 路线按注册顺序选首个可执行动作。
若二者都注册, 连招内部的单次 E 也会检查许可并记账; 若只注册连招, 整个连招作为一个动作。
直接调用未注册的辅助方法仍走普通 Python 路径, 不会自动参与评分或记账。
首次注册前保存的旧方法引用也不会自动变成受管调用; 使用角色当前方法调用即可参与 Planner。

动作应返回 `True`、`False`、`None` 或 `ActionResult`。False 表示失败, True 和 None 表示成功,
ActionResult 按自身 success 判断; 未写 return 也算正常完成。注册方法直接调用时保留其原返回值,
被阻止时返回 False。需要用返回值写布尔分支时, 请显式返回 True/False。
内部动作失败不会自动传给组合函数; 需要时判断内部结果并 return False。
参数只写在方法调用处; 默认目录和方法路线都不额外传参, 因此所需参数应有默认值。
需要不同默认参数或许可的两种技能形态时, 可分别写成两个有意义的方法注册, 参考 Akane。

## 发布队友路线

以下代码放在 entry 或动作方法中, a、b 是已找到的队友:

```python
self.combat_context.request_route(
    [
        Planner.Route.optional(a.click_ultimate),
        Planner.Route.action(a, slot=Planner.ActionSlot.SKILL),
        Planner.Route.reaction(a, b, optional=True),
        b,
    ],
    reason="team setup",
    timeout=8,
)
```

这条路线依次尝试 A 的可选 Q、指定 E、A → B 的可选反应, 再让 B 执行一轮普通 entry。
传角色对象表示完整一轮出招, 不表示仅切入。

| 步骤 | 效果 |
| --- | --- |
| `a.click_skill` | 切到 A 后按默认参数调用方法; 不要求注册, 已注册时沿用登记信息。 |
| `Planner.Route.optional(a.click_skill)` | 指定方法不可用或返回 False 时跳过。 |
| `Planner.Route.action(a, slot=..., tags=..., optional=False)` | 从 A 的动作中按 slot/tag 选择。至少提供一种筛选; 同时提供时两者都要满足, tags 内命中任意一个即可。 |
| `Planner.Route.reaction(a, b, optional=False)` | 从 A 触发对 B 的反应, 等待实际确认。A 不在场时先切入 A。 |
| `a` | 执行 A 的完整普通 entry。 |

`action`、`reaction` 默认都是必需步骤。必需动作不可用时短暂普攻等待, 等待普攻不完成该步骤;
返回 False 时停在该步, 下次调度重试。可选步骤不可用或失败时跳过整步;
不会因为首个候选执行失败, 就在本次尝试其他候选。route 不读取 priority_ready。
参数不匹配、条件函数或方法抛异常等程序错误不会被 optional 吞掉。

方法步骤可以直接使用未注册的组合函数, 函数内照常写条件和循环, 并返回执行结果。
它仅作为路线步骤记账, 不加入普通动作目录或评分, 不自动安装方法包装。
未注册方法没有 slot、tags 和额外执行条件; 需要这些能力时再注册。
其内部调用已注册动作时, 仍检查那些动作自身的执行条件和 Reserve。
方法与已注册动作使用同一结果规则: 正常结束且未返回 False 时完成步骤,
返回 ActionResult 时按其 success 判断。

可选反应在来源 A 实际在场后检查资源和元素配对。不足时跳过;
确认切入 B 却未确认反应时, 可选则跳过, 必需则重试。
死亡目标可由可选步骤跳过, 必需步骤则结束为失败。
路线完成后继续最后在场角色的普通 entry, 并遵守已有协作请求和 return_to_source 设置。

新路线在当前 entry 返回后启用, 不打断发布者的后续代码, 也不会被当前 entry 的动作提前完成:

```python
self.combat_context.request_route(teammate.click_skill)
self.skill_done = True
self.click_ultimate()  # 满足自身条件和 Reserve 时正常尝试。
# 希望马上结束当前 entry 时, 在这里 return。
```

已生效的旧路线要等当前 entry 结束后才被新路线替换。多次发布时后发布的路线替换前面的路线;
等待启用不会延长其期限。`timeout` 与 `until=无参条件` 二选一, 不指定则没有该期限限制。
route 默认不强制返场; `return_to_source=True` 会在完成后提高发布者的回场优先级。

## 保留动作和窗口

只想让队友保留 E 时, 发布 Reserve。它不负责切人, 也不推进路线:

```python
context = self.combat_context
context.reserve_actions(
    [Planner.Reserve.action(teammate, slot=Planner.ActionSlot.SKILL)],
    until=self.is_record_expire,
    reason="hold teammate E",
)
```

保留用于队友, 不限制发布者自己。它按角色对象和 slot 匹配, 限制该队友该槽位的所有动作;
当前路线明确要求的动作可以消费保留。
`until` 必填, 使用无参条件或 `Planner.NEVER_EXPIRES`。永久保留持续到 reset, 不接受 on_finish。
多个槽位写多个 `Planner.Reserve.action(...)`。

需要“路线完成后仍保留技能, 到窗口结束才释放”时使用窗口:

```python
timer = self.clock.timer(timeout=6)
window = self.combat_context.open_window(until=timer.expired, reason="team record window")
window.reserve_actions(
    [Planner.Reserve.action(teammate, slot=Planner.ActionSlot.ULTIMATE)]
)
route = window.request_route(
    [Planner.Route.action(teammate, slot=Planner.ActionSlot.SKILL, optional=True)]
)
self.record_window = window
```

子路线完成或失败不关闭窗口; 窗口到期结束尚未完成的子路线和保留。
窗口可保存在角色上, 在后续 entry 中继续使用, 不必重新传入 context。
到期后追加非空请求会报错。需要多阶段协作时, 在子路线的完成回调中通过仍有效的窗口发布下一条路线。

## 入场诉求和其他协作

在 combat_plan 中用 `plan.add(FieldClaim.high(reason="burst window"))` 提高角色回场意愿。
low、normal、high、critical 是普通评分档位; `FieldClaim.strict(reason="return now")`
要求下一次调度优先切入该角色,
但已生效的严格路线优先。claim 不打断正在运行的 entry, 多个 claim 也不叠加评分。
普通 claim 可加 `expected_entry=ExpectedEntry(slot=Planner.ActionSlot.ULTIMATE)` 优先尝试 Q;
strict claim 切入后仍走普通 entry。

| 请求 | 用途 |
| --- | --- |
| `context.request_switch(teammate)` | 下一次普通调度优先切给指定角色, 不指定动作。 |
| `context.request_role(Planner.Role.SUPPORT)` | 选择辅助定位的角色; 多人匹配时按普通评分选择。 |
| `context.request_tags({Planner.ActionTag.SUPPORT}, count=2)` | 请求两个不同队友各完成一次支援类动作; 每个角色最多计一次, 默认排除发布者。 |

`request_role` 匹配角色定位, `request_tags` 匹配动作标签。tag 请求可用
`avoid_source=False` 包含发布者, 可用 return_to_source 请求完成后返场。
这些请求与 route 都支持 until 和 on_finish; 它们不采用新 route 的延迟启用规则。

随队伍长期生效的策略写在无参 `combat_policies()` 中, Planner reset 时会调用它。
临时请求放在 entry、动作或生命周期回调中。combat_plan、评分条件、describe_role、
switch_in_guard 和到期条件只做查询, 不发布请求或发送输入。

目标角色可用无 context 参数的 guard 延迟普通切入:

```python
def switch_in_guard(self, from_char, has_intro):
    return SwitchInGuard.delay_until_ready(
        condition=self.skill_available,
        timeout=1.5,
        reason="wait for entry state",
    )
```

从 `src.combat.planner` 导入 `FieldClaim`、`ExpectedEntry`、`SwitchInGuard` 等所需类型。
guard 条件满足或超时都会继续切人; strict route 和 strict claim 跳过该等待。

## 查询状态与处理完成

`self.combat_context` 在当前队伍运行周期内稳定可读。reset 后应重新使用角色上的 context,
旧 context 和窗口不能再发布请求。

- `has_active_request()`: 查询协作请求, 包括已提交但等待启用的 route; 不包含窗口本身、纯保留或纯切人请求。
- `has_strict_route()`: 只报告已经启用的严格路线。
- `strict_route_wants_action(char, slot=..., tags=...)`: 当前路线是否正在要求该动作。
- `is_slot_available(char, slot=...)`: 查询 slot 保留限制, 不包含冷却和 can_execute 检查。

这些查询读取已提交状态, 不触发执行或清理, 也不是 entry 必须立即返回的指令。
请求通常返回 handle; 空路线或空保留返回 None。可订阅无参回调:

```python
if route is not None:
    route.on_fulfilled(self.on_route_complete)
    route.on_failed(self.on_route_failed)
    route.on_expired(self.on_route_expired)
```

上述回调是角色自己定义的方法。`on_finish` 响应首次完成、失败或到期, 不重复调用。
可用 `is_pending`、`is_fulfilled`、`is_failed`、`is_expired`、`is_closed` 查询状态;
`when.fulfilled`、`failed`、`expired`、`closed`、`any` 可直接作为其他请求的 until 条件。
closed 表示请求不再影响调度, 与成功、失败、到期分开; 请求回场时可能已经 fulfilled 但尚未 closed。
需要持续窗口语义时使用 open_window, 不依靠已完成路线的到期回调维持保留。
回调可以继续发布请求; 单个回调异常不会丢弃同批其余通知, 处理后异常会向任务上抛。

## 计时与外置代码兼容

长动作使用 `self.clock.timer(timeout=...)` 和 `expired()` 控制时长, 需要时调用 check_combat。
`self.clock.now()` 返回统一单调时间, 不使用 time.time/monotonic/perf_counter。
首次登场逻辑可用 `self.is_first_engage()` 或全场只消费一次的 `self.consume_first_engage()`。
详见[运行期时钟约定](../../runtime_clock.md)。

旧 `ActionIntent`、`FollowupStep`、`ActionReservation`、`click_*_action`、`planner_action` 及带 context 的旧 hook
仍由兼容层支持, 已标记 deprecated, 仅供尚未迁移的外置代码使用。
旧动作声明仍保留 None 算失败的原语义。新角色按本文的方法注册与直接调用编写。

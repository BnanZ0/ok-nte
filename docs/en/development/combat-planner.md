# Combat Planner Development Guide

> **Tip**: Concrete character implementations can be found in [`src/char`](../../../src/char), or viewed in the
> [GitHub](https://github.com/BnanZ0/ok-nte/tree/main/src/char) and
> [CNB](https://cnb.cool/BnanZ0/ok-nte-update/-/tree/main/src/char) code directories.

The planner is the team's brain. A character declares one `CombatPlan`:

- `actions`: the action catalog visible to the planner, used for switch scoring and route/request/reservation matching.
- `claims`: `FieldClaim` entry requests that express "I should be switched in now".
- `entry`: the Python generator action flow for an ordinary entry. When omitted, actions run in declaration order.

Import public types from `src.combat.planner`. This is a sample of commonly used types; add or
remove names as needed:

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

`src.combat.planner` exports only the official development API. Character code must not import internal modules such as `planner/core.py`, `planner/requests.py`, or `planner/state.py` directly.
This guide focuses on the character-author API. `BaseCombatTask` manages the switch execution
lifecycle; character code does not need to call `CombatPlanner` executor methods directly.

## Quick Entry

An ordinary character usually only needs to override `describe_role()` and `combat_plan(context)`:

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

For complex action ordering, define entry flow with action variables in the same plan. An action can run only once in one entry; use `repeat_for_entry()` for a limited extra execution:

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

`yield action` executes the action and sends an `ActionResult` back to the generator. Its truth value is True only when the action succeeded, so you can write:

```python
a = yield action_a
b = yield action_b

if a and b:
    yield action_c

if a and not b:
    yield fallback_action
```

## CombatPlan

Create an empty plan with `self.plan()`, then pass it to the character's Q/E helpers.
Each helper registers the action and returns a callable with the native execution
method's parameter names and types. Planner only receives ordinary `ActionIntent`
objects; release parameters and their binding stay in `BaseChar`.

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

`plan.add(...)` returns its argument and ignores `None`. Actions are registered in
insertion order, which is also their default execution order without `entry`.
Register actions and claims only while building the plan. A call such as `skill(...)`
binds one attempt; it neither executes nor registers another action.
The existing `self.plan(*actions, claims=None, entry=None)` form remains supported:
helpers without a plan still return callable action declarations that can be passed
there. Use `yield action` for the existing parameters and `yield action(...)` only
when supplying release arguments for that attempt. Empty `()` are unnecessary.

Rules:

- Only declare actions and entry requests when creating a plan; do not send input.
- Do not publish one-time coordination requests while creating a plan. Publish `request_route()`, `request_switch()`, `request_role()`, `request_tags()`, and temporary `reserve_actions()` during action execution or after a successful result in the entry flow. Put team-wide long-lived policies in `combat_policies()`.
- Requests published by an entry flow are collected when it next yields or finishes. A request published after receiving a result still takes effect if the flow returns immediately.
- `actions` is the catalog used for scoring and coordination matching; `entry` is the ordinary entry execution flow.
- Multiple independent `claims` may be passed. They do not stack scores; the planner uses only the highest-priority matching claim for the current character.
- Strict routes, expected entries, and active requests take scheduling priority over ordinary entry flow.
- An ordinary entry flow is subject to the planner's per-entry action limit.
- The same action is executed at most once during one entry.

### Execution parameters

Write release parameters on the returned action, not on `click_skill_action(...)`
or `click_ultimate_action(...)`. Completion comes from `click_skill`/`click_ultimate`
through one generic wrapper in `BaseChar`; adding a native method parameter does not
require updating a planner type or a second Q/E parameter signature.

```python
if (yield skill(down_time=0.3, post_sleep=0)):
    yield ultimate(send_click=False)
yield skill(down_time=0.01).repeat_for_entry()
```

These calls create ordinary action copies whose `execute` closures capture the
arguments. Identity, tags, slot, readiness, cooldown checks, and permissions remain
unchanged. A changed parameter does not permit a second execution in the same entry;
apply `repeat_for_entry()` to the bound attempt explicitly.
Unspecified native parameters use their method defaults, except E keeps its existing
helper default of `down_time=0.01`. Argument binding rejects unknown names without
sending input. Constructing a plan and binding an attempt never run the executor.

Q/E helpers always bind the native `click_skill`/`click_ultimate` method. They do not
accept an `execute` replacement; custom executors belong in `planner_action(...)`.
For state-dependent release arguments, calculate them at the action call site.

If ordinary execution and strict routes should share configured parameters, bind them
before registering the action:

```python
skill = self.click_skill_action()  # Create without automatic registration.
skill = plan.add(skill(down_time=self.skill_down_time, post_sleep=0.2))
# Inside entry:
yield skill
yield skill(down_time=0.1, post_sleep=0.2).repeat_for_entry()
```

Bound actions retain their callable type and native parameter completion, so the same
variable can be reused. Supplying arguments again binds a new attempt using the native
method and helper defaults; it does not merge the previous action's argument values.

Catalog scheduling uses those bound values. Binding captures argument values at that
point; it does not evaluate them again at execution. Entry may bind a different attempt
with `skill(...)`, but if a strict route already executed the same action, entry receives
that earlier result instead of releasing it again. `plan.add()` only appends: do not
register the default version first and then append a same-identity parameter version
when the intent is to configure a single catalog action.

### Custom actions

The same mechanism preserves a custom execution function's parameters after its
first `CombatContext` argument:

```python
def perform_burst(self, context: CombatContext, *, duration: float = 0.5) -> bool:
    self.continues_normal_attack(duration)
    return True

# Inside combat_plan:
burst = self.planner_action(
    plan=plan,
    tags=Planner.ActionTag.DAMAGE,
    execute=self.perform_burst,
)
# Inside entry:
yield burst(duration=0.8)
```

Custom calls have the same completion, binding, permission, result, and repeat rules.
The default catalog executor receives only `context`, so extra parameters should have
defaults. Read current state inside that executor for dynamic defaults. Callable-valued
custom arguments are passed through as values; planner does not interpret them.

## ActionIntent

`ActionIntent` expresses "what the character may try after entering the field". Do not split normal attacks, waits, or repeated presses into many actions; keep those details inside one action's `execute` function.

BaseChar's helpers return callable action declarations compatible with `ActionIntent`.
Calling one creates a bound `ActionIntent`; no Q/E-specific parameter classes are
part of the planner API.

Fields:

- `tags: set[ActionTag]`: action meaning and scoring basis.
- `execute: Callable[[CombatContext], ActionResult | bool | None]`: the actual action.
- `name: str = ""`: advanced exact matching and log name.
- `slot: ActionSlot | None = None`: action slot. Coordination routes and reservations should preferably match by slot.
- `reason: str = ""`: planner log and switch reason.
- `can_execute: Callable[[CombatContext], bool] | None`: a hard restriction at planner level.
- `priority_ready: Callable[[CombatContext], bool] | None`: used only for switch scoring.

`action.repeat_for_entry()` lets the action be yielded again in the same entry while preserving its execution, slot, tags, and `can_execute` restrictions. It is suitable for a limited flow such as `Q -> E -> try E once more`; the returned copy should normally be yielded only in the entry flow and should not be added to `CombatPlan.actions`.

Every `yield` counts toward the action limit for one entry. Do not yield it inside a long-running loop; such loops should call `context.is_action_allowed(self, action)` before invoking an existing action helper. This keeps the loop under character-code control while still honoring planner `can_execute` and reservation rules.

When an action has a `slot`, the planner automatically checks reservations through `context.is_slot_available(...)`. A developer-provided `can_execute` only needs to express additional mechanic restrictions. To pre-check a complete action outside an entry flow, use `context.is_action_allowed(self, action)`; it checks both `can_execute` and slot reservations. Ordinary or limited entry actions should still be yielded directly.

`execute` return rules:

- Return `True`: success.
- Return `False` / `None` / no `return`: failure.
- Return `ActionResult`: use `ActionResult.success`.
- Truthy values such as `1` or `"ok"` are not treated as success.

Ordinary characters do not need to construct `ActionResult` manually. Create one only when a custom result name, tags, slot, or reason is required:

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

`ActionResult.tags` and `slot` also participate in coordination matching; ordinary actions should
let a helper create the result automatically.

## ActionTag

`ActionTag` expresses action meaning and scoring. It must not express a mechanism belonging to one specific character.

Common tags:

- `ULTIMATE_ACTION`: Q.
- `SKILL_ACTION`: E.
- `ARC_ACTION`: Arc action, scored as 0.
- `DAMAGE`: Generic damage action.
- `SUPPORT`: Support, healing, or buff action.
- `TEAM_BUFF`: A key team-wide buff. Use only when the buff should be cast before the main DPS ultimate.
- `HIGH_PRIORITY`: Significantly increases an action's switch score. Use for a small number of actions that are especially valuable to attempt; it does not change action order after a character enters.
- `FIELD_TIME`: A planner-built field-time action; characters should not declare it themselves.
- `LEGACY_COMBO`: Legacy combo action.
- `DEFAULT_ACTION`: Low-value fallback entry.

Switch scoring does not add all actions for the same character together; the planner uses the highest-scoring ready action as that character's representative. Tags do not control ordinary entry flow; `CombatPlan.entry` does.

## ActionSlot

`ActionSlot` is the action slot used for coordination matching and is preferred over action names.

Common slots:

- `SKILL`: E.
- `ULTIMATE`: Q.
- `ARC`: Arc.
- `ENTRY_REACTION`: Entry or ring reaction, not a key action.
- `FIELD_TIME`: Planner-built field time.
- `LEGACY_COMBO`: Legacy combo.
- `CUSTOM`: Special action.

Prefer writing coordination and reservations as:

```python
FollowupStep.for_action(zero, ActionSlot.SKILL)
ActionReservation.for_action(nanally, ActionSlot.SKILL)
ActionReservation.for_slots(nanally, {ActionSlot.SKILL, ActionSlot.ULTIMATE})
context.is_slot_available(self, ActionSlot.SKILL)
```

## RoleProfile

`describe_role()` returns a `RoleProfile` describing the character's team role and ordinary
field-time preference. `role` and `field_preference` are separate: `request_role()` matches the
former, while the latter affects ordinary switch scoring. Neither is a hard scheduling command.

| Field | Values / purpose |
|---|---|
| `role` | `Planner.Role.MAIN_DPS`, `SUB_DPS`, or `SUPPORT`; describes the team role. Defaults to `SUB_DPS`. |
| `field_preference` | `Planner.FieldPreference.MAIN_DPS`, `SUB_DPS`, `SUPPORT`, or `SETUP_ONLY`; expresses ordinary field-time preference. When omitted, construction derives the same-named preference from `role`. Explicit values take precedence. |
| `max_field_time` | When greater than 0, allows the planner's built-in field-time fallback for up to this duration; set to 0 to disable it. Defaults to 1.5 seconds. |
| `combat_start_priority` | Used only for the opening switch. Values greater than 0 make a character eligible, and higher values rank first. It does not affect ordinary combat switch scoring. Defaults to 0. |

`MAIN_DPS` favors staying on field, `SUB_DPS` favors shorter field time, and `SUPPORT` and
`SETUP_ONLY` suit characters that rotate out after support or setup actions. Coordination requests
and field claims still affect the final choice.

Ordinary characters only need to declare their role: `MAIN_DPS`, `SUB_DPS`, and
`SUPPORT` each default to the same-named field preference. Special preferences remain
independently configurable:

```python
RoleProfile(role=Planner.Role.MAIN_DPS)
RoleProfile(
    role=Planner.Role.SUB_DPS,
    field_preference=Planner.FieldPreference.SETUP_ONLY,
)
```

Defaults are resolved at construction; changing `role` later does not automatically
change `field_preference`.

## BaseChar Helpers

### `switch_in_guard`

The target character can override `switch_in_guard()` to delay an ordinary switch until its own
entry condition is ready:

```python
def switch_in_guard(self, context, from_char, has_intro):
    return SwitchInGuard.delay_until_ready(
        condition=self.entry_state_ready,
        timeout=1.5,
        reason="waiting for entry state",
    )
```

The switch proceeds when `condition` returns True. It also proceeds after the timeout, so this is a
bounded delay rather than a veto. `poll_interval` controls how often the condition is checked, and
`while_waiting` can provide a callback during the wait. The default guard allows an immediate
switch. Strict routes and strict field claims skip this wait and the ordinary switch cooldown; do
not rely on the guard to protect an entry that must use strict scheduling.

### Combat Session and First Engagement

`BaseCombatTask.begin_combat_session()` is the unified entry point when combat officially starts. It creates the public `task.combat_session`, makes the initial switch decision, and records the actual starting character. `CombatPlanner` only decides the initial switch target; it does not send input or manage the session. `CombatSession.combat_start` is the time combat began; `use_ultimate` and `switch_enabled` are fixed strategies for the current combat.

At the start of `BaseChar.perform()`, the first character to execute actual combat logic in the current battle is recorded. Character logic can use:

```python
if self.is_first_engage():
    # First character to actually engage in this battle
    ...

if self.consume_first_engage():
    # Returns True only once for the whole battle
    ...
```

`is_first_engage()` remains stable during the battle; `consume_first_engage()` returns `True` only once. Neither depends on initial-switch duration or a time window. `task.combat_session` creates a default session on first access. If a task must disable the initial switch and all later switches, set `task.combat_session.switch_enabled = False` before calling `begin_combat_session()`; do not replace the switching method at runtime.

### `click_ultimate_action`

```python
ultimate = self.click_ultimate_action(
    plan,
    name=None,
    tags=None,
    add_tags=None,
    reason="ultimate action available",
    can_execute=None,
)
# Inside entry:
yield ultimate(send_click=False, wait_if_no_cd=0.5)
```

- Registers `slot=ActionSlot.ULTIMATE`, default tag `ULTIMATE_ACTION`, and the default name `"{character}_ultimate"`.
- `tags` replaces the base tags; `add_tags` extends them with one tag or a collection.
- Eligibility always includes `ultimate_available()` plus any extra `can_execute` condition; readiness uses `ultimate_available()`.
- The executor is always `click_ultimate`; supply release arguments on `ultimate(...)`. Bind and register an attempt when catalog scheduling also needs those arguments.
- Omit `plan` when returning the declaration through the existing `self.plan(...)` form.

### `click_skill_action`

```python
skill = self.click_skill_action(
    plan,
    name=None,
    tags=None,
    add_tags=None,
    reason="skill action available",
    can_execute=None,
)
# Inside entry:
yield skill(down_time=0.3, post_sleep=0.2)
```

- Registers `slot=ActionSlot.SKILL`, default tag `SKILL_ACTION`, and the default name `"{character}_skill"`.
- Tag overrides work as for Q. Eligibility always includes `skill_available()` plus any extra `can_execute` condition; readiness uses `skill_available()`.
- The executor is always `click_skill`; supply release arguments on `skill(...)`. E's existing helper default `down_time=0.01` is retained.

### `click_arc_action`

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

This helper sets `slot=ActionSlot.ARC` and defaults to `ActionTag.ARC_ACTION`. Its default
`priority_ready` is False, so an arc action does not by itself attract a switch; it can still run
through the ordinary entry flow after the character is on field. Pass `priority_ready` when it
should contribute to switch scoring.

### `planner_action`

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

Use this to create a custom action. Long actions should be completed inside `execute`.

## FieldClaim

`FieldClaim` expresses "I should be switched in"; it is not an action. `low`, `normal`, `high`, and `critical` raise the ordinary entry score. `strict` selects that character at the next switch decision. After the character enters, the planner still chooses an action from `actions`, a strict route/request, or `entry`.

An ordinary claim can use `ExpectedEntry(slot=...)` to prioritize a slot after returning to the field.
For advanced exact matching, set `action_name` or create the expectation with
`ExpectedEntry.from_action(action)`. A strict claim requests the switch only and does not accept an
`expected_entry`.

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

Declare a strict claim in `combat_plan()` when the character must return within a time window:

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

The planner reads candidate claims again at each switch decision. A locked strict route takes precedence; a strict claim then takes precedence over entry reactions, active requests, and ordinary scoring. If several characters declare strict claims, the planner selects among them by ordinary score and last action time. A strict claim takes effect after the current character finishes its action; it does not interrupt an action. It requests only the switch and does not set an `expected_entry`; after arrival, the character follows its ordinary `entry` flow.

Usage guidance:

- If only Q/E is available, no `FieldClaim` is needed; the action itself participates in scoring.
- Use an ordinary `FieldClaim` to take the field back later; use `FieldClaim.strict()` when it must return at the next switch decision.
- `FieldClaim.critical()` remains an ordinary scoring level and does not force a switch.
- Add `expected_entry` to an ordinary claim when a specific action should be prioritized after taking the field back.
- Multiple `FieldClaim` objects can express independent mechanic entry points; the planner does not add claim scores and selects the highest matching level.

## `combat_policies`

`combat_policies(context)` defines policies that remain active across the team lifecycle. The planner calls it when resetting the current team. It is suitable for permanent reservations, not a temporary window that appears only after this Q/E succeeds.

```python
def combat_policies(self, context: CombatContext):
    context.reserve_actions(
        [ActionReservation.for_action(zero, ActionSlot.SKILL)],
        reason="reserve Zero skill",
        until=Planner.NEVER_EXPIRES,
    )
```

## Coordination Requests

Publish coordination requests after an action executes successfully, or from `combat_policies()` for long-lived policies.

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

Common APIs:

- `context.request_route(...)`: A fixed-order coordination route.

`FollowupStep.for_switch(target, wait_for_turn=True)` switches in and waits for the target's normal turn by default:

```python
context.request_route([
    FollowupStep.for_switch(a),
    FollowupStep.for_action(b, ActionSlot.ULTIMATE),
])
```

`FollowupStep.for_action(target, slot, ...)` creates an action step for a character. With
`optional=True`, the planner skips the step if no matching allowed, ready action is available when
the target arrives; the default required step keeps waiting. Use `required_tags` or `action_names`
to add match conditions. Prefer a slot for ordinary coordination; action names are an advanced
exact-match option. A tag condition matches when the action has at least one requested tag.

To require an entry or ring reaction when the target switches in, use:

```python
FollowupStep.for_entry_reaction(target, reason="trigger entry reaction")
```

A finishes its normal entry flow before the route advances to B's ultimate.
If A is already on field, it still runs its turn. No first action or entry reaction is
required, and action permissions and reservations still apply. A turn uses the normal
flow termination conditions and action limit, including field-time fallback when no
action succeeds. Completion does not require a particular skill to succeed. Exceptions
do not complete the step; an expired or replaced route is not advanced afterward.

For arrival-only behavior, use `FollowupStep.for_switch(a, wait_for_turn=False)`.
The step completes on successful arrival or when the target is already on field.
A single-step route then releases normal flow; a multi-step route advances immediately,
**without waiting for A's turn or guaranteeing that A performs any action**.

Both modes inherit strict-route priority and lifetime. A new route replaces the existing
route, so this is not merely a higher-priority `request_switch()`. Use the latter for
ordinary independent switch requests.

- `context.request_switch(...)`: Request that the next ordinary dispatch switches to a character.
- `context.request_role(...)`: Request that the next ordinary dispatch switches to a character with a team role. When several characters match, ordinary switch scoring chooses one. It does not specify an action or interrupt the current entry flow.
- `context.reserve_actions(...)`: Reserve teammate actions.
- `context.request_tags(...)`: Request one successful matching action from each of a number of teammates. Any one requested tag matches, and each character counts at most once. Use `count` to choose the number of teammates; by default, the requester cannot satisfy its own request.
  Set `avoid_source=False` to let the requester satisfy its own request.

`request_role(Planner.Role.SUPPORT)` asks for any support-role character; `request_tags({Planner.ActionTag.SUPPORT})` asks for any support-type action. The former suits "bring in any support character", while the latter suits "let any teammate perform one healing/buff action".
`request_route()` and `request_tags()` can set `return_to_source=True` to raise the requester's
switch priority after completion.

```python
context.request_role(Planner.Role.SUPPORT, reason="need a support role")
```

These requests usually return a `RequestHandle`. Use it to check completion or expiration, or to
tie one request's lifetime to another. For example, release a reservation after a route completes:

```python
route = context.request_route(steps, until=self.window_expired)
if route is not None:
    route.on_fulfilled(self.on_route_complete)
    route.on_expired(self.on_route_expired)
    context.reserve_actions(reservations, until=route.when.fulfilled)
```

`handle.when.fulfilled`, `expired`, `closed`, and `any` are conditions that can be passed to another
request's `until=`; `is_pending`, `is_fulfilled`, `is_expired`, and `is_closed` query the current state.
`handle.status` is `Planner.RequestStatus.PENDING`, `FULFILLED`, or `EXPIRED`; `closed` is a separate
state. Use `on_finish()` for the first completion or expiration signal, or `on_fulfilled()` and
`on_expired()` to handle the outcomes separately. The `until` argument defaults to None for
`request_route()`, `request_switch()`, `request_role()`, and `request_tags()`, meaning they do not
expire from a window condition. `reserve_actions()` requires
`until`: pass a callable or `Planner.NEVER_EXPIRES` to keep it active until the planner resets.
Completing a route's steps and expiration of its window are separate signals.
An `until=Planner.NEVER_EXPIRES` reservation does not end automatically and cannot use `on_finish`.

`CombatContext` also provides `has_active_request()`, `has_strict_route()`, and
`strict_route_wants_action(char, slot=..., action_name=..., tags=...)` for advanced character logic
that branches on current coordination. Pure reservations and pure switch requests do not count as
active action requests.

## Behavior Summary

- Switch scoring and ordinary entry execution are separate.
- Scoring uses the highest-scoring ready action in `actions`, then adds `FieldClaim`, request, and field-preference scores.
- The current character's ordinary entry execution is controlled by `entry`; without an entry, actions run in declaration order.
- `priority_ready=False` only reduces switch attractiveness; it does not hard-block execution.
- `can_execute=False` is a hard block; a blocked entry action receives a failure result and is not actually executed.
- Strict routes, expected entries, and active requests take priority over ordinary entry flow.
- `ActionResult.tags` does not control entry flow.


## Runtime clock

Characters inherit `self.clock` from `BaseChar`. `self.clock.now()` returns
`TimePoint`, a `float` subclass supporting ordinary numeric comparisons,
arithmetic and formatting. Character state, cooldowns, freeze records and planner
bookkeeping use the same monotonic source; durations stay in seconds.
`self.now()` remains a compatibility alias for external characters.
Freeze helpers and planner time parameters also accept numeric monotonic seconds.
Prefer `clock.UNSET` or `None` for missing events; freeze helpers retain legacy
numeric sentinel support. See [Runtime clock](../../runtime_clock.md) for
arithmetic, storage boundaries and checks.

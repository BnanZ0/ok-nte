# Combat Planner Guide

Characters describe their actions and entry routines. Planner chooses switch targets and runs team routes.
See [src/char](https://github.com/BnanZ0/ok-nte/tree/main/src/char) for built-in examples.
Import public APIs from `src.combat.planner`; do not manipulate Planner's internal state.

## A minimal character

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

An ordinary entry attempts Q, then E, in registration order. If no registered action succeeds and coordination
allows it, Planner can fall back to normal attacks. `max_field_time=0` disables this fallback;
it does not limit a burst loop written by the character. Switch scoring uses the character's
highest-scoring ready action. It neither sums every action nor sets entry order.

Common `RoleProfile` settings:

| Field | Effect |
| --- | --- |
| `role` | Team role: `Planner.Role.MAIN_DPS`, `SUB_DPS`, or `SUPPORT`. Defaults to SUB_DPS. |
| `field_preference` | Ordinary switching preference. Inferred from role unless specified; `Planner.FieldPreference.SETUP_ONLY` is also available. |
| `max_field_time` | Normal-attack fallback budget, default 1.5 seconds. |
| `combat_start_priority` | Positive values participate in the initial combat switch; higher wins. Does not affect ordinary switch scores. |

## Control entry order and parameters

Set `plan.entry` to a method with no required arguments. Call character methods directly,
keeping their native parameter completion and return values:

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

Every call is a new attempt, rechecking its registered conditions and Reserve. Q/E helpers also
check cooldown. There is no entry
call deduplication or action-count limit, and route results are not replayed into later calls.
Ordinary E uses the native 0.05-second press default; a character may override its own defaults.

Entry follows normal Python control flow. A request does not force the function to return;
subsequent registered actions still follow Planner permissions. Even when a later action is
blocked and returns False, assignments, waits, and ordinary helpers do not automatically stop.
Write `return` when the entry should end early.

## Register actions

`register_skill(plan)`, `register_ultimate(plan)`, and `register_arc(plan)` register
`click_skill`, `click_ultimate`, and `click_arc`. They return `None`.
Q/E helpers supply slots, default tags, and cooldown checks. Support's Q/E helpers also add
SUPPORT. Arc does not attract a switch by default. Characters still explicitly set
`self.planner_handles_arc = True` when Planner should handle arc; registration does not change it.
Without takeover, the default character entry may also cast arc.

Use an ordinary method for custom or composite actions:

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

These options also apply to Q/E/arc helpers, except that their slots are fixed:

| Option | Purpose |
| --- | --- |
| `name` / `reason` | Action name, logging, and switch explanation. Usually no custom name is needed. |
| `tags` / `add_tags` | Replace default tags or append tags. Tags affect scoring and coordination matching. |
| `can_execute` | No-argument hard condition. False prevents execution. Read coordination through self.combat_context. |
| `priority_ready` | No-argument scoring condition. False excludes this action from switch scoring, but permits execution. |
| `slot` | Coordination slot for plan.register(), such as Planner.ActionSlot.SKILL. |

`plan.register()` does not infer slots, tags, or cooldown checks from method names.
Common tags include SKILL_ACTION, ULTIMATE_ACTION, SUPPORT, TEAM_BUFF, HIGH_PRIORITY, and DAMAGE.
SKILL_ACTION and ULTIMATE_ACTION already include skill damage scoring; adding DAMAGE is unnecessary.

Register each method once per plan. Different methods may share a slot. A full E combo and a
single E press can both use SKILL; a slot route picks the first executable match in registration
order. If both are registered, the E inside the combo also checks permissions and records results.
If only the combo is registered, the combo is one action. Direct calls to unregistered helpers
remain ordinary Python calls and do not automatically participate in scoring or result recording.
References saved before first registration do not automatically become managed calls; call the
character's current method to participate in Planner execution.

Return `True`, `False`, `None`, or `ActionResult`. False means failure; True and None mean success.
ActionResult uses its own success flag. Finishing without a return also counts as success.
Direct calls preserve the original return value; blocked calls return False. Return an explicit
True/False when the caller needs a Boolean branch. Inner action failures do not automatically
propagate through a combo function; check the inner result and return False when needed.
Pass parameters at the call site. Default catalogs and method routes
supply no extra arguments, so required parameters need defaults. Different skill variants can be
separate meaningful methods with their own parameters and permissions, as in Akane.

## Request teammate routes

Use this inside an entry or action method; a and b are teammates already found by the character:

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

This attempts A's optional Q, its specified E, an optional A-to-B reaction, then B's ordinary entry.
A character object means a complete entry, not arrival alone.

| Step | Effect |
| --- | --- |
| `a.click_skill` | Switch to A and call the method with its defaults. Registration is optional; registered methods retain their metadata. |
| `Planner.Route.optional(a.click_skill)` | Skip if unavailable or returning False. |
| `Planner.Route.action(a, slot=..., tags=..., optional=False)` | Select A's action by slot/tags. Supply at least one filter. Both filters must match when provided; any one tag matches. |
| `Planner.Route.reaction(a, b, optional=False)` | Trigger the specified reaction from A to B and await confirmation. Switch to A first if necessary. |
| `a` | Run A's complete ordinary entry. |

Both action and reaction default to required. An unavailable required action waits with brief
normal attacks that do not complete the step; False keeps it for the next dispatch. An optional step skips when unavailable
or unsuccessful. A failed first candidate does not cause another candidate to be tried in that
attempt. Routes do not read priority_ready. Argument mismatches and exceptions from predicates
or method bodies remain program errors; optional does not suppress them.

Method steps can call unregistered combo functions with ordinary conditions and loops inside.
Their results are recorded for the route, without adding them to the ordinary action catalog,
scoring, or installing method wrappers. Unregistered methods have no slot, tags, or additional
execution predicates; register them when those features are needed. Registered actions called
inside still check their own execution conditions and Reserve. As with registered actions,
The same result rules apply: finishing normally without returning False completes the step;
an ActionResult uses its own success flag.

An optional reaction checks resources and element compatibility after A is actually on field.
It skips if unavailable. Arrival at B without a confirmed reaction skips an optional step and
retries a required step. Optional steps can skip dead targets; required steps end as failed.
After completion, continue the final on-field character's ordinary entry, subject to existing
coordination requests and return_to_source.

A route published during an entry activates after that entry returns. It neither interrupts
remaining code nor consumes actions from the publishing entry:

```python
self.combat_context.request_route(teammate.click_skill)
self.skill_done = True
self.click_ultimate()  # Attempt normally if its conditions and Reserve allow it.
# Write return here if the current entry should end immediately.
```

An existing route is replaced only after the current entry ends. Later publications replace earlier
routes; waiting for activation does not extend a deadline. Choose either `timeout` or a no-argument
`until` condition. Omit both for no such deadline. A route does not force a return by default;
`return_to_source=True` raises the requester's switch priority after completion.

## Reserve actions and share a window

Use Reserve when a teammate should hold E. It neither switches characters nor progresses a route:

```python
context = self.combat_context
context.reserve_actions(
    [Planner.Reserve.action(teammate, slot=Planner.ActionSlot.SKILL)],
    until=self.is_record_expire,
    reason="hold teammate E",
)
```

Reservations apply to teammates, not the requester itself. They match a character object and
slot, affecting all the teammate's actions in that slot.
An action explicitly requested by the current route can consume the reservation.
`until` is required: use a no-argument condition or `Planner.NEVER_EXPIRES`. Permanent holds last
until reset and do not accept on_finish. Reserve multiple slots with multiple Reserve.action entries.

Use a window when holds must survive route completion and last until the window ends:

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

A child route completing or failing does not close the window. Expiration ends unfinished child
routes and holds. Save a window on the character to reuse it in later entries without passing a
context around. Adding nonempty requests after expiration raises an error. For multi-stage
coordination, publish another route from a child route's completion callback while the window remains live.

## Request field entry and other coordination

Inside combat_plan, use `plan.add(FieldClaim.high(reason="burst window"))` to raise entry priority.
low, normal, high, and critical are ordinary scoring levels. `FieldClaim.strict(reason="return now")`
asks for the next dispatch,
behind an already active strict route. Claims do not interrupt running entries or sum their scores.
An ordinary claim can add `expected_entry=ExpectedEntry(slot=Planner.ActionSlot.ULTIMATE)` to
attempt Q first. Strict claims enter through the ordinary entry routine.

| Request | Purpose |
| --- | --- |
| `context.request_switch(teammate)` | Prefer that teammate at the next ordinary dispatch, without specifying an action. |
| `context.request_role(Planner.Role.SUPPORT)` | Choose a support-role character; ordinary scoring resolves multiple matches. |
| `context.request_tags({Planner.ActionTag.SUPPORT}, count=2)` | Ask two different teammates to complete a support-type action. Each counts once; the requester is excluded by default. |

request_role matches character roles; request_tags matches action tags. A tag request can include
the requester with avoid_source=False or ask for a return with return_to_source.
These requests and routes support until and on_finish; the other requests do not use the new
route's deferred activation rule.

Put team-lifetime policies in no-argument `combat_policies()`, called on Planner reset.
Publish temporary requests from entries, actions, or lifecycle callbacks. combat_plan, scoring
conditions, describe_role, switch_in_guard, and deadline conditions only query state; they must
not publish requests or send input.

The target character can delay an ordinary switch with a guard that takes no context argument:

```python
def switch_in_guard(self, from_char, has_intro):
    return SwitchInGuard.delay_until_ready(
        condition=self.skill_available,
        timeout=1.5,
        reason="wait for entry state",
    )
```

Import FieldClaim, ExpectedEntry, SwitchInGuard, and other types as needed from src.combat.planner.
The switch continues when ready or when the timeout elapses. Strict routes and strict claims skip
this wait.

## Query state and handle completion

`self.combat_context` stays available throughout the current team cycle. After reset, use the
character's new context; old contexts and windows cannot publish requests.

- `has_active_request()`: Coordination requests, including committed routes awaiting activation; excludes the window itself, pure holds, and switch requests.
- `has_strict_route()`: Only an already active strict route.
- `strict_route_wants_action(char, slot=..., tags=...)`: Whether the current step requests this action.
- `is_slot_available(char, slot=...)`: Slot reservation permission, without cooldown or can_execute checks.

Queries read committed state without driving execution or cleanup. They are not instructions for
the entry to return immediately. Requests normally return a handle; empty routes or holds return
None. Subscribe no-argument callbacks:

```python
if route is not None:
    route.on_fulfilled(self.on_route_complete)
    route.on_failed(self.on_route_failed)
    route.on_expired(self.on_route_expired)
```

The callbacks above are methods defined by the character. on_finish runs once for the first
completion, failure, or expiration. Query is_pending, is_fulfilled, is_failed, is_expired, and
is_closed; when.fulfilled, failed, expired, closed, and any are predicates usable as another
request's until. closed means the request no longer affects dispatch and is separate from success,
failure, and expiration. A return request may be fulfilled while still not closed.
Use open_window for a continuing window lifetime, rather than an already completed route's
expiration callback. Callbacks may publish requests. One callback exception does not discard
other notifications in the batch; the error propagates to the task after processing.

## Timing and external-code compatibility

Bound long actions with `self.clock.timer(timeout=...)` and expired(), checking combat as needed.
`self.clock.now()` uses the shared monotonic clock; do not call time.time/monotonic/perf_counter.
Use `self.is_first_engage()` for the first acting character or `self.consume_first_engage()` to
consume that event once per combat. See [Runtime clock](../../runtime_clock.md).

Deprecated `ActionIntent`, `FollowupStep`, `ActionReservation`, `click_*_action`, `planner_action`, and
context-taking hooks remain supported for external code awaiting migration. Old action declarations
retain their original None-means-failure behavior. New characters use the method registration
and direct calls shown in this guide.

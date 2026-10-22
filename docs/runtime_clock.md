# Runtime clock

`clock.now()` returns numeric monotonic seconds as `TimePoint`, a lightweight
`float` subclass. It reads `time.monotonic()` in one place. Runtime timestamps,
durations and configuration values support normal numeric operations.

```python
from src.utils import clock

started = clock.now()
elapsed_seconds = clock.elapsed(started)
```

Characters inherit `self.clock` from `BaseChar`; tasks inherit it from
`BaseNTETask`. Both use `self.clock.now()`,
`self.clock.elapsed(start)` and `self.clock.UNSET` without importing the module.
`BaseChar.now()` preserves numeric monotonic seconds for legacy external
characters, including comparisons with numeric sentinels and ordinary numeric
arithmetic. Built-in characters use `self.clock` directly.
Task mixins use the same accessor; task-owned helpers use `self.task.clock`, and
heist paths forward `self.clock` to their task. Import the clock module only in
clock providers and standalone components without a task. Independent data
classes may still need clock defaults, and type annotations may import
`TimePoint` directly. Character state, cooldown snapshots,
freeze records, planner bookkeeping, scene caches, input throttling, audio
triggering, request retirement and MIDI playback all share this clock domain.

Sound triggers can be initialized before a task is bound. They use the shared
clock module directly through their own `clock` attribute, independent of task
binding. Reuse an existing clock accessor when it is reliably available;
otherwise import the shared module directly rather than adding dynamic lookups
or fallback logic just to avoid an import.

Restart the application after changing clock APIs or shared task base classes.
Built-in task hot reload reloads only the selected task module, leaving imported
dependencies and existing base classes cached in the process.

## Numeric behavior

`TimePoint` supports ordinary numeric comparison, arithmetic, formatting and
conversion. Arithmetic results follow normal `float` semantics.

```python
started = self.clock.now()
valid = started >= 0
elapsed_seconds = self.clock.now() - started
offset_seconds = 100 - started
text = f"{started:.3f}"
elapsed_from_number = self.clock.elapsed(100.0)
```

`clock.elapsed()`, combat freeze helpers, planner time parameters and log gates
accept numeric monotonic seconds. They do not require a particular timestamp
class or validate which clock produced a number. Keep values from the same
monotonic source; Unix timestamps and performance-counter readings have separate
reference points. Shared clock accessors and the source scan maintain this rule.

`clock.UNSET` represents an event which has not happened. It is a numeric
negative infinity with false boolean value and infinite elapsed age. A genuine
zero returned by `clock.now()` remains valid and true in boolean conditions.
New code should prefer `clock.UNSET` or `None` for missing events; legacy numeric
sentinels are also accepted by combat freeze helpers. A missing or negative
freeze start retains the existing 10000-second cooldown result and adds no
freeze record.

`BaseChar.now()` delegates to the same clock for legacy external characters.
Old comparisons such as `self.now() < 0` and numeric timestamps passed through
character or task freeze helpers work without a separate conversion adapter.
Built-in characters continue to use `self.clock` directly.

## Calendar time and external data

Use timezone-aware `datetime` for actual dates, daily schedules and wall-clock
records. Convert to/from Unix timestamps at the storage or external API boundary.
Keep existing stored schemas compatible. Never persist a runtime `TimePoint` or
try to reconstruct one from a Unix timestamp or a fixed wall/monotonic offset.

Mouse recording measures holds and gaps with runtime points while keeping its
existing numeric `time`/`end_time` fields at the serialization boundary. Monthly
card scheduling keeps timezone-aware calendar dates. File modification times and
MIDI note offsets retain their existing meanings.

Third-party libraries may keep their own clocks. Do not exchange raw clock
readings with them; pass durations when possible. Existing `sleep`, event waits
and asyncio scheduling APIs are unchanged.

## Interval timers

Use `self.clock.timer()` to measure elapsed time or check a timeout. It reads
`time.monotonic()` by default; `precise=True` selects `time.perf_counter()` for
performance measurements and precise waits. Both modes expose the same methods
and include sleep time. Each timer keeps its selected clock for its lifetime
and exposes only durations, never a raw timestamp or a `TimePoint`.

```python
timer = self.clock.timer(timeout=10)
do_something()
cost_seconds = timer.elapsed()
timed_out = timer.expired()
remaining_seconds = timer.remaining()

precise = self.clock.timer(timeout=0.005, precise=True)
precise_cost_seconds = precise.elapsed()
precise_timed_out = precise.expired()
```

Create the timer before the whole operation, including any initial task sleep.
`timer(timeout=seconds)` calculates its deadline once at creation. `expired()`
only reads the clock and compares it with that deadline; `remaining()` returns
the remaining seconds, clamped to zero. Use this form for local timeout loops
instead of calculating and comparing `clock.now() + seconds` yourself.

`extend(seconds)` moves the deadline forward without resetting elapsed time.
Launcher updates and heist lock picking use this to exclude their wait time
from the operation's timeout budget. Reading a timer does not modify it;
concurrent deadline extensions require synchronization by the caller.

For elapsed-only measurements, omit `timeout`. Calling `expired()` or
`remaining()` without a configured timeout raises `ValueError`.
`expired(seconds)` remains available to check an explicit interval from the
original start, without modifying the configured deadline. For a fresh interval,
create a new timer.
Characters, task helpers and standalone components use the same clock accessors
described above. Ordinary cooldowns, freeze accounting and shared state continue
to use `clock.now()` and `TimePoint`.

Heist route sleep uses `timer(timeout=seconds, precise=True)` across both task sleep and its
existing final busy wait. Character loading uses it for elapsed-cost statistics.
Clock resolution does not guarantee thread wakeup or input timing accuracy.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.TestCombatSession tests.TestLauncherTask tests.test_sound_trigger_capture tests.test_log_gate
```

These suites cover combat session timestamps, launcher timeout extensions,
sound initialization and throttling, and log throttling. Only
`src/utils/clock.py` should read raw clocks; callers use the shared accessors.

Type annotations describe numeric timing values for editors. There is no
dedicated clock type-check job or checker dependency.

Tests can inject numeric monotonic seconds directly or patch the single
`src.utils.clock._read_monotonic` source. Precise timer tests can independently
patch `src.utils.clock._read_perf_counter`. Avoid real sleeps in timing tests.

"""A small explicit-edge pipeline for task scene automation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ok import WaitFailedException

from src.utils import clock

SceneDetector = Callable[[], bool]
SceneAction = Callable[[], Any]
FailureRoute = Callable[["StepFailure"], Enum | None]
TransitionAction = Callable[[], Any]


class SceneFlowConfigError(ValueError):
    """Raised when a SceneFlow pipeline declaration is invalid."""


class SceneReplan(Exception):
    """Leave an interrupted action and classify the screen again."""


@dataclass(frozen=True)
class StepPolicy:
    """Retry limits for one explicit pipeline step."""

    max_attempts: int | None = None
    interval: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts is not None and self.max_attempts <= 0:
            raise SceneFlowConfigError("Step policy max_attempts must be greater than zero")
        if self.interval < 0:
            raise SceneFlowConfigError("Step policy interval cannot be negative")


@dataclass(frozen=True)
class StepFailure:
    step: Enum
    reason: str
    attempts: int
    error: Exception | None = None


@dataclass(frozen=True)
class SceneTransition:
    """Actively drive a completed step toward one of its explicit successors."""

    action: TransitionAction
    interval: float
    timeout: float | None


@dataclass(frozen=True)
class SceneFlowSnapshot:
    current_step: Enum | None
    active_steps: tuple[Enum, ...]
    transition_step: Enum | None
    last_failure: StepFailure | None
    attempts: Mapping[Enum, int]
    interrupt_count: int
    unknown_duration: float
    recovery_count: int


@dataclass(frozen=True)
class _Step:
    key: Enum
    detector: SceneDetector
    action: SceneAction
    next_steps: tuple[Enum, ...]
    policy: StepPolicy
    transition: SceneTransition | None
    on_failure: FailureRoute | None
    priority: int
    order: int


@dataclass(frozen=True)
class _Guard:
    key: Enum
    detector: SceneDetector
    action: SceneAction
    priority: int
    order: int


@dataclass(frozen=True)
class _Interrupt:
    detector: SceneDetector
    handler: Callable[[], Any]
    priority: int
    order: int


@dataclass(frozen=True)
class _Recovery:
    action: Callable[[], Any]
    grace: float
    interval: float
    max_attempts: int
    timeout: float


class SceneFlow:
    """Run explicit scene steps while keeping recovery outside task business code."""

    DEFAULT_POLL_INTERVAL = 0.05

    def __init__(self) -> None:
        self._steps: dict[Enum, _Step] = {}
        self._guards: list[_Guard] = []
        self._interrupts: list[_Interrupt] = []
        self._recovery: _Recovery | None = None
        self._before_step: Callable[[], Any] | None = None
        self._passthrough_exceptions: tuple[type[BaseException], ...] = ()
        self._order = 0
        self._run_depth = 0
        self._handling_interrupt = False
        self._interrupt_recovery_pending = False
        self._active_steps: tuple[Enum, ...] = ()
        self._current_step: Enum | None = None
        self._transition_source: Enum | None = None
        self._transition_started_at: clock.TimePoint | None = None
        self._active_transition: SceneTransition | None = None
        self._attempts: dict[Enum, int] = {}
        self._next_attempt_at = clock.UNSET
        self._next_transition_at = clock.UNSET
        self._last_failure: StepFailure | None = None
        self._interrupt_count = 0
        self._unknown_since: clock.TimePoint | None = None
        self._recovery_count = 0
        self._last_recovery_at: clock.TimePoint | None = None

    @property
    def active(self) -> bool:
        return self._run_depth > 0

    @property
    def handling_interrupt(self) -> bool:
        return self._handling_interrupt

    @property
    def snapshot(self) -> SceneFlowSnapshot:
        unknown_duration = (
            0.0 if self._unknown_since is None else max(0.0, clock.now() - self._unknown_since)
        )
        return SceneFlowSnapshot(
            current_step=self._current_step,
            active_steps=self._active_steps,
            transition_step=self._transition_source if self._active_transition else None,
            last_failure=self._last_failure,
            attempts=dict(self._attempts),
            interrupt_count=self._interrupt_count,
            unknown_duration=unknown_duration,
            recovery_count=self._recovery_count,
        )

    def step(
        self,
        key: Enum,
        detector: SceneDetector,
        action: SceneAction,
        *,
        next: tuple[Enum, ...],
        policy: StepPolicy | None = None,
        transition: SceneTransition | None = None,
        on_failure: FailureRoute | None = None,
        priority: int = 0,
    ) -> "SceneFlow":
        self._require_enum(key, "step key")
        self._require_callable(detector, "step detector")
        self._require_callable(action, "step action")
        if key in self._steps or any(guard.key == key for guard in self._guards):
            raise SceneFlowConfigError(f"SceneFlow key is already registered: {key!r}")
        next_steps = self._normalize_steps(next, "step next")
        step_policy = policy or StepPolicy()
        if not isinstance(step_policy, StepPolicy):
            raise SceneFlowConfigError("Step policy must be a StepPolicy")
        if transition is not None and not isinstance(transition, SceneTransition):
            raise SceneFlowConfigError("Step transition must be a SceneTransition")
        if on_failure is not None:
            self._require_callable(on_failure, "step failure route")
        self._steps[key] = _Step(
            key,
            detector,
            action,
            next_steps,
            step_policy,
            transition,
            on_failure,
            priority,
            self._next_order(),
        )
        return self

    def transition(
        self,
        action: TransitionAction,
        *,
        interval: float = 0.0,
        timeout: float | None = None,
    ) -> SceneTransition:
        """Create a direct action used while waiting for a step's successors."""
        self._require_callable(action, "transition action")
        if interval < 0:
            raise SceneFlowConfigError("Scene transition interval cannot be negative")
        if timeout is not None and timeout <= 0:
            raise SceneFlowConfigError("Scene transition timeout must be greater than zero")
        return SceneTransition(action, interval, timeout)

    def guard(
        self,
        key: Enum,
        detector: SceneDetector,
        action: SceneAction,
        *,
        priority: int = 0,
    ) -> "SceneFlow":
        self._require_enum(key, "guard key")
        self._require_callable(detector, "guard detector")
        self._require_callable(action, "guard action")
        if key in self._steps or any(guard.key == key for guard in self._guards):
            raise SceneFlowConfigError(f"SceneFlow key is already registered: {key!r}")
        self._guards.append(_Guard(key, detector, action, priority, self._next_order()))
        return self

    def before_step(self, action: Callable[[], Any]) -> "SceneFlow":
        self._require_callable(action, "before-step action")
        self._before_step = action
        return self

    def propagate(self, *exception_types: type[BaseException]) -> "SceneFlow":
        if not exception_types:
            raise SceneFlowConfigError(
                "SceneFlow requires at least one exception type to propagate"
            )
        for exception_type in exception_types:
            if not isinstance(exception_type, type) or not issubclass(
                exception_type, BaseException
            ):
                raise SceneFlowConfigError(
                    "SceneFlow propagated exceptions must inherit BaseException"
                )
        self._passthrough_exceptions += exception_types
        return self

    def interrupt(
        self,
        detector: SceneDetector,
        handler: Callable[[], Any],
        *,
        priority: int = 0,
    ) -> "SceneFlow":
        self._require_callable(detector, "interrupt detector")
        self._require_callable(handler, "interrupt handler")
        self._interrupts.append(_Interrupt(detector, handler, priority, self._next_order()))
        return self

    def recovery(
        self,
        action: Callable[[], Any],
        *,
        grace: float = 5.0,
        interval: float = 2.0,
        max_attempts: int = 30,
        timeout: float = 60.0,
    ) -> "SceneFlow":
        self._require_callable(action, "recovery action")
        if grace < 0:
            raise SceneFlowConfigError("Scene recovery grace cannot be negative")
        if interval < 0:
            raise SceneFlowConfigError("Scene recovery interval cannot be negative")
        if max_attempts <= 0:
            raise SceneFlowConfigError("Scene recovery max_attempts must be greater than zero")
        if timeout <= 0 or grace >= timeout:
            raise SceneFlowConfigError("Scene recovery timeout must be greater than grace")
        self._recovery = _Recovery(action, grace, interval, max_attempts, timeout)
        return self

    def safe_point(self) -> None:
        if self._check_interrupts():
            raise SceneReplan()

    def run(
        self,
        until: Callable[[], bool],
        *,
        start: Enum,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> bool:
        self._validate(start)
        self._require_callable(until, "run condition")
        if poll_interval < 0:
            raise SceneFlowConfigError("SceneFlow poll interval cannot be negative")

        self._reset_run_state(start)
        self._run_depth += 1
        try:
            while not until():
                self._run_before_step()
                if self._interrupt_recovery_pending:
                    self._interrupt_recovery_pending = False
                    if not self._recover_if_needed(force=True):
                        return False
                    self._wait(poll_interval)
                    continue
                if self._check_interrupts():
                    self._wait(poll_interval)
                    continue

                guard = self._find_guard()
                if guard is not None:
                    if not self._run_guard(guard):
                        return False
                    self._wait(poll_interval)
                    continue

                step = self._find_active_step()
                if step is None:
                    if self._transition_timed_out():
                        if not self._fail_transition("step transition timeout"):
                            return False
                    elif self._active_transition is not None:
                        if not self._run_transition():
                            return False
                    elif not self._recover_if_needed():
                        return False
                    self._wait(poll_interval)
                    continue

                self._unknown_since = None
                self._finish_transition()
                if step.key != self._transition_source:
                    self._attempts[step.key] = 0
                if step.key == self._transition_source and clock.now() < self._next_attempt_at:
                    self._wait(poll_interval)
                    continue
                if self._attempt_limit_reached(step):
                    if not self._fail_step(step, "step retry limit reached"):
                        return False
                    self._wait(poll_interval)
                    continue

                self._current_step = step.key
                self._transition_source = step.key
                self._transition_started_at = None
                self._attempts[step.key] = self._attempts.get(step.key, 0) + 1
                if not self._run_step(step):
                    return False
                self._wait(poll_interval)
            return True
        finally:
            self._run_depth -= 1

    def _run_step(self, step: _Step) -> bool:
        try:
            step.action()
        except SceneReplan:
            self._discard_attempt(step)
            self._activate_steps(self._resume_steps(step))
            return True
        except WaitFailedException as error:
            return self._handle_action_failure(step, error)
        except Exception as error:
            if self._must_propagate(error):
                raise
            raise
        self._activate_steps(step.next_steps)
        self._active_transition = step.transition
        self._next_transition_at = clock.UNSET
        if step.transition is not None:
            self._transition_started_at = clock.now()
            return self._run_transition(force=True)
        if step.key in step.next_steps:
            self._next_attempt_at = clock.now() + step.policy.interval
        return True

    def _run_transition(self, *, force: bool = False) -> bool:
        transition = self._active_transition
        source = self._transition_source
        if transition is None or source is None:
            return True
        if not force and clock.now() < self._next_transition_at:
            return True
        try:
            transition.action()
        except SceneReplan:
            return True
        except WaitFailedException as error:
            return self._fail_step(
                self._steps[source], str(error) or error.__class__.__name__, error
            )
        except Exception as error:
            if self._must_propagate(error):
                raise
            raise
        self._next_transition_at = clock.now() + transition.interval
        return True

    def _run_guard(self, guard: _Guard) -> bool:
        try:
            guard.action()
        except SceneReplan:
            return True
        except WaitFailedException as error:
            return self._begin_recovery(
                StepFailure(guard.key, str(error) or error.__class__.__name__, 1, error)
            )
        except Exception as error:
            if self._must_propagate(error):
                raise
            raise
        return True

    def _handle_action_failure(self, step: _Step, error: WaitFailedException) -> bool:
        self._run_before_step()
        if self._interrupt_recovery_pending:
            return True
        if self._check_interrupts():
            return True
        guard = self._find_guard()
        if guard is not None:
            return True
        successor = self._find_step(step.next_steps)
        if successor is not None and successor.key != step.key:
            self._activate_steps(step.next_steps)
            return True
        if step.detector() and not self._attempt_limit_reached(step):
            self._activate_steps((step.key,))
            self._next_attempt_at = clock.now() + step.policy.interval
            return True
        return self._fail_step(step, str(error) or error.__class__.__name__, error)

    def _fail_transition(self, reason: str) -> bool:
        source = self._transition_source
        if source is None:
            return self._begin_recovery()
        return self._fail_step(self._steps[source], reason)

    def _fail_step(self, step: _Step, reason: str, error: Exception | None = None) -> bool:
        failure = StepFailure(step.key, reason, self._attempts.get(step.key, 0), error)
        self._last_failure = failure
        self._run_before_step()
        if self._interrupt_recovery_pending or self._check_interrupts():
            return True
        guard = self._find_guard()
        if guard is not None:
            return True
        successor = self._find_step(step.next_steps)
        if successor is not None and successor.key != step.key:
            self._activate_steps(step.next_steps)
            return True
        if step.on_failure is None:
            return self._begin_recovery(failure)
        next_step = step.on_failure(failure)
        if next_step is None:
            return self._begin_recovery(failure)
        self._require_enum(next_step, "failure route result")
        if next_step not in self._steps:
            raise SceneFlowConfigError(
                f"Step failure route returned an unknown step: {next_step!r}"
            )
        self._activate_start(next_step)
        return True

    def _begin_recovery(self, failure: StepFailure | None = None) -> bool:
        self._finish_transition()
        if failure is not None:
            self._last_failure = failure
        if self._unknown_since is None:
            self._unknown_since = clock.now()
        return self._recover_if_needed()

    def _recover_if_needed(self, *, force: bool = False) -> bool:
        recovery = self._recovery
        if recovery is None:
            return False
        now = clock.now()
        if self._unknown_since is None:
            self._unknown_since = now
        unknown_duration = now - self._unknown_since
        if unknown_duration >= recovery.timeout or self._recovery_count >= recovery.max_attempts:
            return False
        if not force and unknown_duration < recovery.grace:
            return True
        if (
            not force
            and self._last_recovery_at is not None
            and now - self._last_recovery_at < recovery.interval
        ):
            return True
        try:
            succeeded = recovery.action() is not False
        except SceneReplan:
            succeeded = True
        except Exception as error:
            if self._must_propagate(error):
                raise
            succeeded = False
        self._last_recovery_at = now
        self._recovery_count += 1
        return succeeded or self._recovery_count < recovery.max_attempts

    def _check_interrupts(self) -> bool:
        if self._handling_interrupt:
            return False
        for interrupt in sorted(self._interrupts, key=lambda item: (-item.priority, item.order)):
            if interrupt.detector():
                self._handling_interrupt = True
                try:
                    self._interrupt_recovery_pending = interrupt.handler() is False
                    if self._interrupt_recovery_pending:
                        self._last_failure = StepFailure(
                            self._current_step or self._active_steps[0],
                            "interrupt handler failed",
                            0,
                        )
                    self._interrupt_count += 1
                    return True
                finally:
                    self._handling_interrupt = False
        return False

    def _find_guard(self) -> _Guard | None:
        for guard in sorted(self._guards, key=lambda item: (-item.priority, item.order)):
            if guard.detector():
                return guard
        return None

    def _find_active_step(self) -> _Step | None:
        return self._find_step(self._active_steps)

    def _find_step(self, keys: tuple[Enum, ...]) -> _Step | None:
        candidates = [self._steps[key] for key in keys]
        for step in sorted(candidates, key=lambda item: (-item.priority, item.order)):
            if step.detector():
                return step
        return None

    def _activate_steps(self, keys: tuple[Enum, ...]) -> None:
        self._active_steps = keys
        self._next_attempt_at = clock.UNSET

    def _activate_start(self, step: Enum) -> None:
        self._active_steps = (step,)
        self._current_step = None
        self._transition_source = step
        self._transition_started_at = None
        self._finish_transition()
        self._attempts[step] = 0
        self._next_attempt_at = clock.UNSET

    @staticmethod
    def _resume_steps(step: _Step) -> tuple[Enum, ...]:
        return tuple(dict.fromkeys((step.key, *step.next_steps)))

    def _transition_timed_out(self) -> bool:
        source = self._transition_source
        transition = self._active_transition
        if source is None or transition is None or self._transition_started_at is None:
            return False
        return (
            transition.timeout is not None
            and clock.now() - self._transition_started_at >= transition.timeout
        )

    def _finish_transition(self) -> None:
        self._active_transition = None
        self._next_transition_at = clock.UNSET
        self._transition_started_at = None

    def _attempt_limit_reached(self, step: _Step) -> bool:
        maximum = step.policy.max_attempts
        return maximum is not None and self._attempts.get(step.key, 0) >= maximum

    def _discard_attempt(self, step: _Step) -> None:
        attempts = self._attempts.get(step.key, 0)
        if attempts <= 1:
            self._attempts.pop(step.key, None)
        else:
            self._attempts[step.key] = attempts - 1

    def _reset_run_state(self, start: Enum) -> None:
        self._active_steps = (start,)
        self._current_step = None
        self._transition_source = start
        self._transition_started_at = None
        self._finish_transition()
        self._attempts.clear()
        self._next_attempt_at = clock.UNSET
        self._last_failure = None
        self._interrupt_count = 0
        self._unknown_since = None
        self._recovery_count = 0
        self._last_recovery_at = None
        self._interrupt_recovery_pending = False

    def _run_before_step(self) -> None:
        if self._before_step is not None:
            self._before_step()

    def _must_propagate(self, error: BaseException) -> bool:
        return isinstance(error, self._passthrough_exceptions)

    def _validate(self, start: Enum) -> None:
        if not self._steps:
            raise SceneFlowConfigError("SceneFlow requires at least one registered step")
        self._require_enum(start, "run start")
        if start not in self._steps:
            raise SceneFlowConfigError(f"SceneFlow start step is not registered: {start!r}")
        for step in self._steps.values():
            for next_step in step.next_steps:
                if next_step not in self._steps:
                    raise SceneFlowConfigError(
                        "SceneFlow step "
                        f"{step.key!r} references an unknown next step: {next_step!r}"
                    )

    def _next_order(self) -> int:
        order = self._order
        self._order += 1
        return order

    @staticmethod
    def _wait(poll_interval: float) -> None:
        if poll_interval > 0:
            time.sleep(poll_interval)

    @staticmethod
    def _require_callable(value: object, name: str) -> None:
        if not callable(value):
            raise SceneFlowConfigError(f"SceneFlow {name} must be callable")

    @staticmethod
    def _require_enum(value: object, name: str) -> None:
        if not isinstance(value, Enum):
            raise SceneFlowConfigError(f"SceneFlow {name} must be an Enum member")

    def _normalize_steps(self, keys: tuple[Enum, ...], name: str) -> tuple[Enum, ...]:
        if not isinstance(keys, tuple) or not keys:
            raise SceneFlowConfigError(
                f"SceneFlow {name} must be a non-empty tuple of Enum members"
            )
        if len(set(keys)) != len(keys):
            raise SceneFlowConfigError(f"SceneFlow {name} cannot contain duplicate steps")
        for key in keys:
            self._require_enum(key, name)
        return keys

"""Planner-owned invocation scopes, command submission and lifecycle notifications."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Literal, cast

from .context import CombatContext
from .errors import _cleanup_with, _note_error, _raise_errors
from .requests import _Request, _RoleRequest, _RouteRequest, _WindowRequest
from .state import CombatState, _PlanSnapshot
from .types import RequestStatus, _Action

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask

    from .core import _EntrySession

_Phase = Literal["build", "read", "event", "policy", "entry", "action", "callback"]


@dataclass(slots=True)
class _Frame:
    owner: BaseChar | None
    phase: _Phase
    cache: dict[int, _PlanSnapshot] = field(default_factory=dict)
    registry: dict[Callable, _Action] = field(default_factory=dict)
    session: _EntrySession | None = None
    window: _WindowRequest | None = None

    @property
    def writable(self):
        return self.phase in {"policy", "entry", "action", "callback"}


def operation(phase: _Phase, *, decision: bool = False):
    """Establish a public runtime boundary without changing its Python signature."""

    def decorate[**P, R](method: Callable[P, R]) -> Callable[P, R]:
        parameters = signature(method)
        owner_parameter = list(parameters.parameters)[1]

        @wraps(method)
        def invoke(self, *args, **kwargs):
            owner = parameters.bind(self, *args, **kwargs).arguments[owner_parameter]
            owner = getattr(owner, "target", owner)
            outer = self._operation_depth == 0
            if outer:
                self._settle()
            self._operation_depth += 1
            try:
                while True:
                    version = self._revision
                    with self._frame(owner, phase):
                        result = method(self, *args, **kwargs)
                    if outer:
                        self._settle()
                    if not decision or not outer or version == self._revision:
                        return result
            except BaseException as primary:
                if outer:
                    # Keep committed state and deliver remaining notifications on failure too.
                    try:
                        self._settle()
                    except BaseException as secondary:
                        _note_error(primary, secondary)
                raise
            finally:
                self._operation_depth -= 1

        return cast(Callable[P, R], invoke)

    return decorate


class _PlannerRuntime:
    """Methods mixed into CombatPlanner; there is only one runtime owner."""

    task: BaseCombatTask
    state: CombatState

    def _init_runtime(self):
        self._generation = 0
        self._contexts: dict[int, CombatContext] = {}
        self._frames: list[_Frame] = []
        self._commands: deque[_Request] = deque()
        self._notifications: deque[Callable[[], None]] = deque()
        self._settling = False
        self._operation_depth = 0
        self._entry_depth = 0
        self._action_depth = 0
        self._revision = 0

    @property
    def _active_frame(self) -> _Frame | None:
        return self._frames[-1] if self._frames else None

    @contextmanager
    def _frame(
        self,
        owner: BaseChar | None,
        phase: _Phase,
        *,
        session: _EntrySession | None = None,
        window: _WindowRequest | None = None,
        cache: dict[int, _PlanSnapshot] | None = None,
    ) -> Iterator[_Frame]:
        previous = self._active_frame
        frame = _Frame(
            owner,
            phase,
            cache=cache if cache is not None else (previous.cache if previous else {}),
            session=session,
            window=window,
        )
        self._frames.append(frame)
        try:
            yield frame
        except Exception as error:
            # Argument binding can fail before the character method enters the traceback.
            if owner is not None and not hasattr(error, "_combat_char"):
                setattr(error, "_combat_char", owner)
            raise
        finally:
            self._frames.pop()

    def _reset_runtime(self, chars: Iterable[BaseChar]):
        self.state.reset(())
        for request in self._commands:
            request.close()
        self._commands.clear()
        self._notifications.clear()
        self._generation += 1
        self.state = CombatState(chars=[char for char in chars if char is not None])
        self._contexts = {}
        for char in self.state.chars:
            context = CombatContext(self, self._generation, char)
            self._contexts[id(char)] = context
            char._combat_context = context

    def _validate_context(self, context: CombatContext):
        if (
            context._generation != self._generation
            or self._contexts.get(id(context.current_char)) is not context
        ):
            raise RuntimeError("combat context belongs to an inactive planner cycle")

    def context_for(self, current_char: BaseChar) -> CombatContext:
        context = self._contexts.get(id(current_char))
        if context is None:
            raise ValueError("character does not belong to the current planner cycle")
        self._validate_context(context)
        return context

    def _read[R](self, context: CombatContext, query: Callable[[], R]) -> R:
        self._validate_context(context)
        if self._active_frame is not None:
            return query()
        with self._frame(context.current_char, "read"):
            return query()

    def _evaluate[R](self, owner: BaseChar, callback: Callable[..., R], *args) -> R:
        with self._frame(owner, "read"):
            return callback(*args)

    def _describe_role(self, owner):
        return self._evaluate(owner, owner.describe_role)

    def _metadata_registered(self, owner, action):
        frame = self._active_frame
        if frame is not None and frame.phase == "build" and frame.owner is owner:
            frame.registry[action._registered_method] = action

    def _method_call(self, owner, method, args, kwargs):
        context = self._contexts.get(id(owner))
        if context is None or context is not getattr(owner, "_combat_context", None):
            return method(*args, **kwargs)
        frame = self._active_frame
        if frame is None:
            with _cleanup_with(self._settle):
                self._settle()
                with self._frame(owner, "action"):
                    return self._method_call(owner, method, args, kwargs)
        if frame.phase == "build":
            action = frame.registry.get(method)
        else:
            action = self._resolve_route_method(owner, method)
        if not frame.writable and action is not None:
            raise RuntimeError("input methods cannot run in a read-only planner callback")
        if action is None:
            return method(*args, **kwargs)
        return self._invoke_registered_method(owner, action, args, kwargs)

    def _session_for(self, owner: BaseChar) -> _EntrySession | None:
        return next(
            (
                f.session
                for f in reversed(self._frames)
                if f.session is not None and f.session.char is owner
            ),
            None,
        )

    def _bind_callback[R](
        self, context: CombatContext, callback: Callable[[], R]
    ) -> Callable[[], R]:
        def invoke():
            self._validate_context(context)
            outer = self._active_frame is None
            with _cleanup_with(self._settle if outer else lambda: None):
                with self._frame(context.current_char, "callback"):
                    return callback()

        return invoke

    def _notify(self, callback):
        self._notifications.append(callback)

    def _bind_listener(self, context, callback):
        self._validate_context(context)
        return self._bind_callback(context, callback)

    def _submit_request(self, context: CombatContext, request: _Request):
        self._validate_context(context)
        frame = self._active_frame
        if frame is None or not frame.writable:
            raise RuntimeError(
                "planner requests require a policy, entry, action or lifecycle callback"
            )
        request.parent = frame.window
        if isinstance(request, _RoleRequest):
            request._describe = self._describe_role
        for reservation in getattr(request, "reservations", ()):
            target = reservation._target
            if target is not None and not any(target is char for char in self.state.chars):
                raise ValueError("reserved character does not belong to the current team")
        request.handle._bind_callback = lambda callback: self._bind_listener(context, callback)
        request.handle._dispatch = self._notify
        request.handle._flush = self._flush_notifications
        if request.on_finish is not None:
            request.handle.on_finish(request.on_finish)
        if callable(request.until):
            condition = request.until
            request.until = lambda: self._evaluate(context.current_char, condition)
        self._commands.append(request)

    def _flush_notifications(self):
        if self._active_frame is None and self._operation_depth == 0:
            self._settle()

    def _prune_expired_requests(self) -> None:
        if self.state.prune():
            self._revision += 1

    def _settle(self):
        if self._settling:
            return
        self._settling = True
        errors = []
        try:
            while True:
                self._prune_expired_requests()
                while self._commands:
                    request = self._commands.popleft()
                    if request.expired(self.task.clock.now()):
                        request.finish(RequestStatus.EXPIRED)
                        request.close()
                    elif isinstance(request, _RouteRequest) and self._entry_depth:
                        for previous in self.state.pending_routes:
                            previous.finish(RequestStatus.EXPIRED)
                            previous.close()
                        self.state.pending_routes = [request]
                    else:
                        self.state.add_requests([request])
                    self._revision += 1
                if not self._notifications:
                    if not self._entry_depth and self.state.pending_routes:
                        routes = self.state.pending_routes
                        self.state.pending_routes = []
                        self.state.add_requests(routes)
                        self._revision += 1
                        continue
                    break
                batch = list(self._notifications)
                self._notifications.clear()
                for callback in batch:
                    try:
                        callback()
                    except Exception as error:
                        errors.append(error)
        except Exception as error:
            errors.append(error)
        finally:
            self._settling = False
        _raise_errors(errors)

    def _check_window(self, window):
        if window._generation != self._generation:
            raise RuntimeError("window belongs to an inactive planner cycle")
        context = self.context_for(window._source)
        request = window._request
        if request.handle.is_closed or request.expired(self.task.clock.now()):
            raise RuntimeError("cannot add requests to an expired window")
        return context

    def _window_call(self, window, build):
        frame = self._active_frame
        if frame is None or not frame.writable:
            raise RuntimeError("window requests require a writable planner callback")
        context = self._check_window(window)
        with self._frame(frame.owner, frame.phase, session=frame.session, window=window._request):
            return build(context)

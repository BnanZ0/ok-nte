"""Published planner API adapters; scheduling lives in the method-based runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import wraps
from inspect import Parameter, isgenerator, signature
from typing import TYPE_CHECKING, Callable, Concatenate, Self, cast

from .binding import _original_method
from .errors import _cleanup_with
from .types import (
    ActionExecutor,
    ActionIntent,
    ActionResult,
    FollowupStep,
    RequestStatus,
    _Action,
    _RouteStep,
)

if TYPE_CHECKING:
    from .context import CombatContext
    from .core import _EntrySession

MAX_ACTIONS_PER_ENTRY = 20


def legacy_context_hook[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Keep old super().hook(context) calls working behind the no-argument contract."""

    parameters = signature(method)

    @wraps(method)
    def invoke(owner, *args, **kwargs):
        if "context" in kwargs:
            kwargs.pop("context")
        try:
            parameters.bind(owner, *args, **kwargs)
        except TypeError:
            parameters.bind(owner, *args[1:], **kwargs)
            args = args[1:]
        return method(owner, *args, **kwargs)

    return invoke


def adapt_character_hooks(cls):
    """Adapt published overrides once, using their declared arity, never parameter names."""
    for name, arity in (("combat_plan", 0), ("combat_policies", 0), ("switch_in_guard", 2)):
        method = cls.__dict__.get(name)
        if method is None:
            continue
        parameters = signature(method)
        try:
            parameters.bind(None, *([None] * arity))
        except TypeError:
            parameters.bind(None, None, *([None] * arity))
            takes_context = True
        else:
            # Optional context is positional; keyword-only and variadic options do not count.
            positional = [
                parameter
                for parameter in parameters.parameters.values()
                if parameter.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            ]
            takes_context = len(positional) > arity + 1
        if takes_context:

            def adapt(original, old_parameters):
                values = list(old_parameters.parameters.values())
                modern_parameters = old_parameters.replace(parameters=values[:1] + values[2:])

                @wraps(original)
                def invoke(owner, *args, **kwargs):
                    kwargs.pop("context", None)
                    try:
                        modern_parameters.bind(owner, *args, **kwargs)
                    except TypeError:
                        modern_parameters.bind(owner, *args[1:], **kwargs)
                        args = args[1:]
                    return original(owner, owner.combat_context, *args, **kwargs)

                return invoke

            setattr(cls, name, adapt(method, parameters))
        else:
            setattr(cls, name, legacy_context_hook(method))


@dataclass(slots=True)
class _BoundAction[**P](ActionIntent):
    """Published deferred-argument protocol, isolated from the runtime."""

    bind: Callable[P, ActionExecutor] = field(kw_only=True, repr=False, compare=False)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Self:
        execute = self.bind(*args, **kwargs) if args or kwargs else self.execute
        return replace(self, execute=execute)


def native_action[**P](
    method: Callable[P, bool],
    *,
    name,
    tags,
    add_tags,
    slot,
    reason,
    can_execute=None,
    priority_ready=None,
    availability=None,
    **defaults: object,
) -> _BoundAction[P]:
    method = _original_method(method)
    tags = set(tags)
    if add_tags:
        tags.update(add_tags if isinstance(add_tags, (set, list, tuple)) else {add_tags})
    condition = can_execute

    def bind(*args: P.args, **kwargs: P.kwargs) -> ActionExecutor:
        parameters = signature(method)
        supplied = parameters.bind_partial(*args, **kwargs)
        call_kwargs = {
            key: value for key, value in defaults.items() if key not in supplied.arguments
        }
        call_kwargs.update(kwargs)
        arguments = parameters.bind(*args, **call_kwargs)
        return lambda context: method(*arguments.args, **arguments.kwargs)

    return _BoundAction(
        name=name,
        tags=tags,
        slot=slot,
        reason=reason,
        can_execute=lambda context: (
            (availability is None or availability()) and (condition is None or condition(context))
        ),
        priority_ready=(
            priority_ready
            if priority_ready is not None
            else (lambda _: availability())
            if availability is not None
            else None
        ),
        execute=bind(),
        bind=bind,
        _legacy_method=method,
    )


def custom_action[**P](
    execute: Callable[Concatenate["CombatContext", P], ActionResult | bool | None],
    **metadata,
) -> _BoundAction[P]:
    def bind(*args: P.args, **kwargs: P.kwargs) -> ActionExecutor:
        signature(execute).bind(None, *args, **kwargs)
        return lambda context: execute(context, *args, **kwargs)

    return _BoundAction(execute=execute, bind=bind, **metadata)


def action_definition(action, owner):
    """Compile a published action object without carrying its protocol into core."""

    if not isinstance(action, ActionIntent):
        return action
    method = action._legacy_method
    return _Action(
        name=action.name,
        tags=set(action.tags),
        slot=action.slot,
        reason=action.reason,
        execute=lambda: action.run(owner.combat_context),
        can_execute=lambda: action.is_allowed(owner.combat_context),
        priority_ready=(lambda: action.priority_ready(owner.combat_context))
        if action.priority_ready is not None
        else None,
        _key=action.identity_key(),
        methods=(method,) if method is not None else (),
    )


def route_step(step: FollowupStep) -> _RouteStep:
    return _RouteStep(
        reason=step.reason,
        slot=step.slot,
        required_tags=set(step.required_tags),
        action_names=set(step.action_names),
        target_indices=set(step.target_indices),
        target_names=set(step.target_names),
        requires_entry_reaction=step.requires_entry_reaction,
        optional=step.optional,
        switch_step=step.switch_step,
        wait_for_turn=step.wait_for_turn,
        invalid_status=RequestStatus.EXPIRED,
    )


def route_deadline(context, request, until):
    """Keep the published handle's late-expiration signal outside route execution."""

    if until is None:
        return
    from .requests import _WindowRequest

    deadline = _WindowRequest(
        reason=request.reason,
        _source=request._source,
        until=lambda: request.handle.is_expired or until(),
    )
    deadline.handle.on_expired(lambda: request.handle._finish(RequestStatus.EXPIRED))
    context._publish_request(deadline)


def entry_callable(entry, actions, owner):
    """Normalize the entry factory's result at the public compatibility boundary."""

    def run():
        flow = entry() if entry is not None else iter(actions)
        if flow is None or isinstance(flow, (bool, ActionResult)):
            return
        _run_legacy_flow(flow, owner)

    return run


def _run_legacy_flow(flow, owner):
    """Interpret published yields; modern function calls never enter this adapter."""
    planner = owner.combat_context._planner
    session = cast("_EntrySession", planner._session_for(owner))
    generator = isgenerator(flow)
    result = None
    with _cleanup_with(flow.close if generator else lambda: None):
        for _ in range(MAX_ACTIONS_PER_ENTRY):
            try:
                item = flow.send(result) if generator else next(flow)
            except StopIteration:
                return
            if not isinstance(item, (_Action, ActionIntent)):
                raise TypeError("legacy entry must yield an ActionIntent")
            action = action_definition(item, owner)
            planner._settle()
            result = (
                session.performed_results.get(action.identity_key())
                if isinstance(item, ActionIntent)
                else None
            )
            if result is None:
                result = planner._attempt_entry_action(session, action)
            if not planner._should_continue_entry(owner, result):
                return

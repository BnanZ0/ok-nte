"""Instance action bindings, independent of plan snapshots."""

from functools import wraps
from types import MethodType
from typing import Callable, cast


def _original_method[T](method: T) -> T:
    return cast(T, getattr(method, "__combat_method__", method))


def _bind_action_method(method: Callable[..., object]) -> None:
    """Install one binding per instance/function, retaining Python method signatures."""
    method = cast(MethodType, method)
    owner = method.__self__
    bindings = vars(owner).get("_planner_action_bindings", {})
    binding = bindings.get(method.__func__)
    if binding is not None:
        return

    # Resolve declared attributes too, including aliases and decorators without wraps.
    candidates = dict.fromkeys(
        [method.__name__, *vars(owner)]
        + [
            name
            for cls in type(owner).__mro__
            for name, value in vars(cls).items()
            if callable(value) and not name.startswith("__")
        ]
    )
    names = [name for name in candidates if _original_method(getattr(owner, name, None)) == method]
    if not names:
        raise TypeError("registered method must be accessible as a character attribute")

    @wraps(method.__func__)
    def invoke(instance, *args, **kwargs):
        context = getattr(instance, "_combat_context", None)
        if context is None:
            return method(*args, **kwargs)
        return context._planner._method_call(instance, method, args, kwargs)

    setattr(invoke, "__combat_method__", method)
    binding = MethodType(invoke, owner)
    for name in names:
        setattr(owner, name, binding)
    vars(owner).setdefault("_planner_action_bindings", {})[method.__func__] = binding

import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable

from src.utils import clock


@dataclass
class _LogState:
    last_time: float | None = None
    last_message: str | None = None


class LogGate:
    """Small helper for throttled logs and message-change logs."""

    def __init__(self, target=None, time_func: Callable[[], float] | None = None):
        self._target = target
        self._time = time_func or clock.now
        self._states: dict[Any, _LogState] = {}
        self._lock = threading.Lock()

    def allow(self, key: Any, interval: float) -> bool:
        """Return True when key has not logged within interval seconds."""

        if interval <= 0:
            return True
        now = self._time()
        with self._lock:
            state = self._states.setdefault(key, _LogState())
            if state.last_time is not None and now - state.last_time < interval:
                return False
            state.last_time = now
            return True

    def allow_message(
        self,
        key: Any,
        message: str,
        interval: float | None,
        changed: bool = False,
    ) -> bool:
        """Return True when a message should be logged for key."""

        if interval is not None and interval <= 0:
            interval = 0
        now = self._time()
        with self._lock:
            state = self._states.setdefault(key, _LogState())
            if changed and state.last_message != message:
                state.last_message = message
                state.last_time = now
                return True
            if interval is None:
                return False
            if (
                interval > 0
                and state.last_time is not None
                and now - state.last_time < interval
            ):
                return False
            state.last_message = message
            state.last_time = now
            return True

    def log(
        self,
        level: str,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        key: Any = None,
        exception: Exception | None = None,
        notify: bool = False,
        stacklevel: int = 1,
    ) -> bool:
        """Log message if allowed by the gate and return whether it was emitted."""

        if key is None:
            key = self.callsite_key(level, stacklevel)
        if not self.allow_message(key, message, interval, changed=changed):
            return False

        self._emit(level, message, exception=exception, notify=notify)
        return True

    def info(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
        key: Any = None,
        stacklevel: int = 1,
    ) -> bool:
        return self.log(
            "info",
            message,
            interval=interval,
            changed=changed,
            key=key,
            notify=notify,
            stacklevel=stacklevel + 1,
        )

    def debug(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
        key: Any = None,
        stacklevel: int = 1,
    ) -> bool:
        return self.log(
            "debug",
            message,
            interval=interval,
            changed=changed,
            key=key,
            notify=notify,
            stacklevel=stacklevel + 1,
        )

    def warning(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
        key: Any = None,
        stacklevel: int = 1,
    ) -> bool:
        return self.log(
            "warning",
            message,
            interval=interval,
            changed=changed,
            key=key,
            notify=notify,
            stacklevel=stacklevel + 1,
        )

    def error(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        exception: Exception | None = None,
        notify: bool = False,
        key: Any = None,
        stacklevel: int = 1,
    ) -> bool:
        return self.log(
            "error",
            message,
            interval=interval,
            changed=changed,
            key=key,
            exception=exception,
            notify=notify,
            stacklevel=stacklevel + 1,
        )

    def _emit(
        self,
        level: str,
        message: str,
        exception: Exception | None = None,
        notify: bool = False,
    ):
        if self._target is None:
            raise RuntimeError("LogGate target is not configured")

        task_log = getattr(self._target, f"log_{level}", None)
        if task_log is not None:
            if level == "error":
                task_log(message, exception, notify=notify)
            else:
                task_log(message, notify=notify)
            return

        log_func = getattr(self._target, level)
        if level == "error":
            log_func(message, exception)
        else:
            log_func(message)

    def reset(self, key: Any = None):
        with self._lock:
            if key is None:
                self._states.clear()
            else:
                self._states.pop(key, None)

    @staticmethod
    def callsite_key(level: str, stacklevel: int = 1) -> tuple[str, int, str]:
        try:
            frame = sys._getframe(stacklevel + 1)
        except ValueError:
            return ("<unknown>", 0, level)
        return (frame.f_code.co_filename, frame.f_lineno, level)


class LogGateMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log_gate = LogGate(self)

    def log_gated(
        self,
        level: str,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        key: Any = None,
        exception: Exception | None = None,
        notify: bool = False,
    ) -> bool:
        return self._log_gate.log(
            level,
            message,
            interval=interval,
            changed=changed,
            key=key,
            exception=exception,
            notify=notify,
            stacklevel=2,
        )

    def log_info_gated(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
    ) -> bool:
        return self._log_gate.info(
            message,
            interval=interval,
            changed=changed,
            notify=notify,
            stacklevel=2,
        )

    def log_debug_gated(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
    ) -> bool:
        return self._log_gate.debug(
            message,
            interval=interval,
            changed=changed,
            notify=notify,
            stacklevel=2,
        )

    def log_warning_gated(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        notify: bool = False,
    ) -> bool:
        return self._log_gate.warning(
            message,
            interval=interval,
            changed=changed,
            notify=notify,
            stacklevel=2,
        )

    def log_error_gated(
        self,
        message: str,
        interval: float | None = 1.0,
        changed: bool = False,
        exception: Exception | None = None,
        notify: bool = False,
    ) -> bool:
        return self._log_gate.error(
            message,
            interval=interval,
            changed=changed,
            exception=exception,
            notify=notify,
            stacklevel=2,
        )

"""One clock domain for runtime timestamps; durations are seconds.

Calendar dates and persisted Unix timestamps belong at I/O boundaries and use
timezone-aware datetime values. Runtime timestamps must never be persisted.
"""

from collections.abc import Callable
from time import monotonic as _read_monotonic
from time import perf_counter as _read_perf_counter


class TimePoint(float):
    """Numeric monotonic seconds; UNSET is false, while a genuine zero is valid."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return self != UNSET


UNSET = TimePoint(float("-inf"))


def now() -> TimePoint:
    """Read the project's runtime clock."""
    return TimePoint(_read_monotonic())


def elapsed(start: float) -> float:
    """Return elapsed seconds; an event that never happened has infinite age."""
    return now() - start


class Timer:
    """Measure intervals with a clock selected once at construction."""

    __slots__ = ("_read_clock", "_started", "_deadline")

    def __init__(self, *, timeout: float | None = None, precise: bool = False) -> None:
        self._read_clock: Callable[[], float] = _read_perf_counter if precise else _read_monotonic
        self._started = self._read_clock()
        self._deadline = None if timeout is None else self._started + timeout

    def elapsed(self) -> float:
        """Return elapsed seconds, including time spent sleeping."""
        return self._read_clock() - self._started

    def expired(self, timeout: float | None = None) -> bool:
        """Compare with the configured deadline, or an explicit interval from start."""
        deadline = self._require_deadline() if timeout is None else self._started + timeout
        return self._read_clock() >= deadline

    def remaining(self) -> float:
        """Return seconds left until the configured deadline, clamped to zero."""
        return max(0.0, self._require_deadline() - self._read_clock())

    def extend(self, seconds: float) -> None:
        """Extend the deadline; callers must synchronize concurrent modifications."""
        self._deadline = self._require_deadline() + seconds

    def _require_deadline(self) -> float:
        if self._deadline is None:
            raise ValueError("Timer has no timeout; specify timeout when creating it")
        return self._deadline


def timer(*, timeout: float | None = None, precise: bool = False) -> Timer:
    """Start a monotonic interval timer; precise=True selects perf_counter()."""
    return Timer(timeout=timeout, precise=precise)

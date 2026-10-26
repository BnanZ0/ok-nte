"""Preserve the original failure while completing planner cleanup and notifications."""

from contextlib import contextmanager
from traceback import format_exception


def _note_error(primary: BaseException, secondary: BaseException) -> None:
    primary.add_note("Additional planner failure:\n" + "".join(format_exception(secondary)))


def _raise_errors(errors: list[Exception]) -> None:
    if errors:
        primary = errors[0]
        for secondary in errors[1:]:
            _note_error(primary, secondary)
        raise primary


@contextmanager
def _cleanup_with(cleanup):
    try:
        yield
    except BaseException as primary:
        try:
            cleanup()
        except BaseException as secondary:
            _note_error(primary, secondary)
        raise
    else:
        cleanup()

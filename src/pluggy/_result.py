"""Result wrapper for hook call outcomes."""
from __future__ import annotations

import sys
from types import TracebackType
from typing import Callable
from typing import Generic
from typing import TypeVar
from typing import cast

__all__ = ["Result"]

ResultType = TypeVar("ResultType")


class Result(Generic[ResultType]):
    """An object used to capture and access the result of a hook call.

    Can be used by hookwrappers to inspect/modify results.
    """

    __slots__ = ("_result", "_exception", "_excinfo")

    def __init__(
        self,
        result: ResultType | None,
        exception: BaseException | None,
    ) -> None:
        self._result = result
        self._exception = exception
        # Store excinfo if there's an exception
        if exception is not None:
            self._excinfo: tuple[type[BaseException], BaseException, TracebackType | None] | None = (
                type(exception),
                exception,
                exception.__traceback__,
            )
        else:
            self._excinfo = None

    @property
    def excinfo(
        self,
    ) -> tuple[type[BaseException], BaseException, TracebackType | None] | None:
        return self._excinfo

    @property
    def exception(self) -> BaseException | None:
        return self._exception

    @classmethod
    def from_call(cls, func: Callable[[], ResultType]) -> Result[ResultType]:
        """Call func() and wrap the outcome in a Result.

        If func() raises, the exception is stored.
        If func() returns, the return value is stored.
        """
        __tracebackhide__ = True
        exception: BaseException | None = None
        result: ResultType | None = None
        try:
            result = func()
        except BaseException as exc:
            exception = exc
        return cls(result, exception)

    def force_result(self, result: ResultType) -> None:
        """Force the result to be this value, clearing any exception."""
        self._result = result
        self._exception = None
        self._excinfo = None

    def force_exception(self, exception: BaseException) -> None:
        """Force the exception to be this value."""
        self._exception = exception
        self._excinfo = (
            type(exception),
            exception,
            exception.__traceback__,
        )
        self._result = None

    def get_result(self) -> ResultType:
        """Get the result or re-raise the exception.

        Re-raising preserves the original traceback.
        """
        __tracebackhide__ = True
        exc = self._exception
        if exc is not None:
            # Reset the traceback to the stored one to avoid traceback accumulation
            exc.__traceback__ = self._excinfo[2] if self._excinfo else None  # type: ignore[union-attr]
            raise exc
        return cast(ResultType, self._result)

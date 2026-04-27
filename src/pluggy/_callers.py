"""Hook caller implementation for multicall."""
from __future__ import annotations

import warnings
from typing import Any
from typing import Callable
from typing import Generator
from typing import Mapping
from typing import Sequence
from typing import TYPE_CHECKING

from pluggy._result import Result

if TYPE_CHECKING:
    from pluggy._hooks import HookImpl


__all__ = ["_multicall", "HookCallError"]


class HookCallError(Exception):
    """Error raised when a hook call fails validation."""

    pass


def _multicall(
    hook_name: str,
    hook_impls: Sequence[HookImpl],
    caller_kwargs: Mapping[str, object],
    firstresult: bool,
) -> object | list[object]:
    """Execute a hook call.

    This function orchestrates calling hookimpls and wrappers in the correct order.

    Wrappers (wrapper=True) yield to get the result directly:
        result = yield  # gets the result or exception is raised

    Legacy hookwrappers (hookwrapper=True) yield to get a Result object:
        outcome = yield  # gets a Result object
        result = outcome.get_result()
    """
    __tracebackhide__ = True

    results: list[object] = []
    exception: BaseException | None = None

    # Teardowns to process: (is_legacy, generator, hookimpl)
    teardowns: list[tuple[bool, Generator[Any, Any, Any], HookImpl]] = []

    try:
        # Process hookimpls in reverse order (last registered calls first)
        for i in range(len(hook_impls) - 1, -1, -1):
            hook_impl = hook_impls[i]

            # Build kwargs for this impl - only include args it accepts
            kwargs: dict[str, object] = {}
            for name in hook_impl.argnames:
                if name in caller_kwargs:
                    kwargs[name] = caller_kwargs[name]
                else:
                    raise HookCallError(
                        f"hook call must provide argument {name!r}"
                    )

            if hook_impl.hookwrapper:
                # Legacy hookwrapper - yields to get a Result object
                gen = hook_impl.function(**kwargs)
                if gen is None:
                    raise TypeError(f"{hook_impl.function!r} did not return a generator")
                try:
                    next(gen)
                except StopIteration:
                    _raise_wrapfail(hook_impl, "did not yield")
                teardowns.append((True, gen, hook_impl))
            elif hook_impl.wrapper:
                # New-style wrapper - yields to get the result directly
                gen = hook_impl.function(**kwargs)
                if gen is None:
                    raise TypeError(f"{hook_impl.function!r} did not return a generator")
                try:
                    next(gen)
                except StopIteration:
                    _raise_wrapfail(hook_impl, "did not yield")
                teardowns.append((False, gen, hook_impl))
            else:
                # Regular hookimpl
                try:
                    res = hook_impl.function(**kwargs)
                except BaseException as e:
                    exception = e
                    break
                if res is not None:
                    results.append(res)
                    if firstresult:
                        break

    except BaseException as e:
        exception = e

    # Compute the outcome
    if exception is None:
        if firstresult:
            outcome: object = results[0] if results else None
        else:
            outcome = results
    else:
        outcome = None

    # Process teardowns in LIFO order
    # All teardowns get either Result (hookwrapper) or direct value (wrapper)

    # We track outcome in a Result so we can modify it
    result_obj = Result(outcome if exception is None else None, exception)

    while teardowns:
        is_legacy, gen, hook_impl = teardowns.pop()

        if is_legacy:
            # Legacy hookwrapper - send Result object
            try:
                gen.send(result_obj)
            except StopIteration:
                pass
            except BaseException as e:
                # Teardown exception - warn and continue
                from pluggy._warnings import PluggyTeardownRaisedWarning
                warnings.warn(
                    PluggyTeardownRaisedWarning(
                        f"A hookwrapper raised in plugin '{hook_impl.plugin_name}' "
                        f"at hook {hook_name!r}:\n{e!r}"
                    ),
                    stacklevel=5,
                )
            else:
                _raise_wrapfail(hook_impl, "has second yield")
        else:
            # New-style wrapper - throw exception or send result
            exc = result_obj._exception
            if exc is not None:
                try:
                    gen.throw(exc)
                except StopIteration as s:
                    # Wrapper swallowed exception and returned a value
                    result_obj.force_result(s.value)
                except RuntimeError as e:
                    # PEP 479: StopIteration in generator -> RuntimeError
                    if isinstance(e.__cause__, StopIteration):
                        result_obj.force_exception(e.__cause__)
                    else:
                        result_obj.force_exception(e)
                except BaseException as e:
                    result_obj.force_exception(e)
                else:
                    _raise_wrapfail(hook_impl, "has second yield")
            else:
                # Send the result value
                try:
                    current_result = result_obj._result
                    gen.send(current_result)
                except StopIteration as s:
                    result_obj.force_result(s.value)
                except RuntimeError as e:
                    # PEP 479: StopIteration in generator -> RuntimeError
                    if isinstance(e.__cause__, StopIteration):
                        result_obj.force_exception(e.__cause__)
                    else:
                        result_obj.force_exception(e)
                except BaseException as e:
                    result_obj.force_exception(e)
                else:
                    _raise_wrapfail(hook_impl, "has second yield")

    return result_obj.get_result()


def _raise_wrapfail(
    hook_impl: HookImpl,
    msg: str,
) -> None:
    """Raise an error for a wrapper failure."""
    func = hook_impl.function
    try:
        name = func.__name__
    except AttributeError:
        name = repr(func)
    try:
        filename = func.__code__.co_filename
        lineno = func.__code__.co_firstlineno
    except AttributeError:
        filename = "?"
        lineno = 0
    raise RuntimeError(
        f"wrap_controller at {name!r} ({filename}:{lineno}) {msg}"
    )

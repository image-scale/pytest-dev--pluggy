"""Hook specification and implementation infrastructure."""
from __future__ import annotations

import inspect
import warnings
from typing import Any
from typing import Callable
from typing import Final
from typing import Generator
from typing import Mapping
from typing import Sequence
from typing import TypedDict
from typing import TypeVar
from typing import final


__all__ = [
    "HookCaller",
    "HookImpl",
    "HookimplMarker",
    "HookSpec",
    "HookspecMarker",
    "varnames",
]


def varnames(func: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return tuple of positional and keyword argument names for a function.

    Returns (argnames, kwonlydefaults)
    - argnames: positional argument names without defaults
    - kwonlydefaults: positional argument names with defaults
    """
    # Handle wrapped functions
    raw_func = func
    while hasattr(raw_func, "__wrapped__"):
        raw_func = raw_func.__wrapped__  # type: ignore[attr-defined]

    # For classes, use __init__
    if inspect.isclass(raw_func):
        try:
            raw_func = raw_func.__init__
        except AttributeError:
            return ((), ())

    # For bound methods, get the underlying function
    if inspect.ismethod(raw_func):
        raw_func = raw_func.__func__

    # For callables that aren't functions, use __call__
    if not inspect.isfunction(raw_func) and not inspect.ismethod(raw_func):
        try:
            raw_func = raw_func.__call__  # type: ignore[union-attr]
        except AttributeError:
            return ((), ())

    # Get the code object
    try:
        code: Any = raw_func.__code__  # type: ignore[union-attr]
    except AttributeError:
        return ((), ())

    # Get defaults
    try:
        defaults: tuple[Any, ...] | None = raw_func.__defaults__  # type: ignore[union-attr]
    except AttributeError:
        defaults = None

    # Get positional argument count and names
    argcount: int = code.co_argcount
    argnames: tuple[str, ...] = code.co_varnames[:argcount]

    # Remove 'self' and 'cls'
    if argnames and argnames[0] in ("self", "cls"):
        argnames = argnames[1:]

    # Split by defaults
    if defaults:
        num_defaults = len(defaults)
        positional = argnames[:-num_defaults] if num_defaults < len(argnames) else ()
        optional = argnames[-num_defaults:] if num_defaults <= len(argnames) else argnames
    else:
        positional = argnames
        optional = ()

    return (positional, optional)


class HookspecOpts(TypedDict, total=False):
    """Options for hookspec markers."""

    firstresult: bool
    historic: bool
    warn_on_impl: Warning | None
    warn_on_impl_args: Mapping[str, Warning] | None


class HookimplOpts(TypedDict, total=False):
    """Options for hookimpl markers."""

    wrapper: bool
    hookwrapper: bool
    optionalhook: bool
    tryfirst: bool
    trylast: bool
    specname: str | None


class HookspecMarker:
    """Decorator for marking functions as hook specifications.

    Instantiate with a project name and use as a decorator
    on hook specification methods.
    """

    def __init__(self, project_name: str) -> None:
        self.project_name: Final[str] = project_name

    def __call__(
        self,
        function: Callable[..., object] | None = None,
        firstresult: bool = False,
        historic: bool = False,
        warn_on_impl: Warning | None = None,
        warn_on_impl_args: Mapping[str, Warning] | None = None,
    ) -> Any:
        """Mark a function as a hookspec.

        :param function: The function to mark (can be None for decorator use).
        :param firstresult: If True, hook returns first non-None result.
        :param historic: If True, hook calls are recorded and replayed for late plugins.
        :param warn_on_impl: Warning to raise when an impl is registered.
        :param warn_on_impl_args: Warnings to raise for specific impl args.
        """

        def setattr_hookspec_opts(func: Callable[..., object]) -> Callable[..., object]:
            if historic and firstresult:
                raise ValueError("cannot have a historic firstresult hook")
            opts: HookspecOpts = {
                "firstresult": firstresult,
                "historic": historic,
                "warn_on_impl": warn_on_impl,
                "warn_on_impl_args": warn_on_impl_args,
            }
            setattr(func, self.project_name + "_spec", opts)
            return func

        if function is not None:
            return setattr_hookspec_opts(function)
        return setattr_hookspec_opts


class HookimplMarker:
    """Decorator for marking functions as hook implementations.

    Instantiate with a project name and use as a decorator
    on hook implementation methods.
    """

    def __init__(self, project_name: str) -> None:
        self.project_name: Final[str] = project_name

    def __call__(
        self,
        function: Callable[..., object] | None = None,
        hookwrapper: bool = False,
        optionalhook: bool = False,
        tryfirst: bool = False,
        trylast: bool = False,
        specname: str | None = None,
        wrapper: bool = False,
    ) -> Any:
        """Mark a function as a hookimpl.

        :param function: The function to mark (can be None for decorator use).
        :param hookwrapper: If True, this is a legacy hookwrapper (yields Result).
        :param wrapper: If True, this is a modern wrapper (uses yield to get result).
        :param optionalhook: If True, no error if spec is missing.
        :param tryfirst: If True, call early in hook chain.
        :param trylast: If True, call late in hook chain.
        :param specname: Alternative name to look up the spec.
        """

        def setattr_hookimpl_opts(func: Callable[..., object]) -> Callable[..., object]:
            opts: HookimplOpts = {
                "wrapper": wrapper,
                "hookwrapper": hookwrapper,
                "optionalhook": optionalhook,
                "tryfirst": tryfirst,
                "trylast": trylast,
                "specname": specname,
            }
            setattr(func, self.project_name + "_impl", opts)
            return func

        if function is not None:
            return setattr_hookimpl_opts(function)
        return setattr_hookimpl_opts


@final
class HookSpec:
    """A hook specification (signature plus options)."""

    __slots__ = ("namespace", "name", "function", "argnames", "kwargnames", "opts")

    def __init__(
        self,
        namespace: object,
        name: str,
        function: Callable[..., object],
        opts: HookspecOpts,
    ) -> None:
        self.namespace = namespace
        self.name = name
        self.function = function
        self.argnames, self.kwargnames = varnames(function)
        self.opts = opts

    def warn_on_impl(self, hook_impl: HookImpl) -> None:
        """Issue warnings for this hookimpl if any warn_on_impl options are set."""
        warn_on_impl = self.opts.get("warn_on_impl")
        if warn_on_impl is not None:
            warnings.warn_explicit(
                warn_on_impl,
                type(warn_on_impl),
                hook_impl.function.__code__.co_filename,
                hook_impl.function.__code__.co_firstlineno,
            )
        warn_on_impl_args = self.opts.get("warn_on_impl_args")
        if warn_on_impl_args:
            # Warn in the order the args appear in the implementation
            impl_argnames = hook_impl.argnames + hook_impl.kwargnames
            for argname in impl_argnames:
                if argname in warn_on_impl_args:
                    warning = warn_on_impl_args[argname]
                    warnings.warn_explicit(
                        warning,
                        type(warning),
                        hook_impl.function.__code__.co_filename,
                        hook_impl.function.__code__.co_firstlineno,
                    )


@final
class HookImpl:
    """A hook implementation (function plus options plus plugin info)."""

    __slots__ = (
        "plugin",
        "plugin_name",
        "function",
        "argnames",
        "kwargnames",
        "hookwrapper",
        "wrapper",
        "optionalhook",
        "tryfirst",
        "trylast",
        "specname",
    )

    def __init__(
        self,
        plugin: object,
        plugin_name: str,
        function: Callable[..., object],
        hook_impl_opts: HookimplOpts,
    ) -> None:
        self.plugin = plugin
        self.plugin_name = plugin_name
        self.function = function
        self.argnames, self.kwargnames = varnames(function)
        self.hookwrapper = hook_impl_opts.get("hookwrapper", False)
        self.wrapper = hook_impl_opts.get("wrapper", False)
        self.optionalhook = hook_impl_opts.get("optionalhook", False)
        self.tryfirst = hook_impl_opts.get("tryfirst", False)
        self.trylast = hook_impl_opts.get("trylast", False)
        self.specname = hook_impl_opts.get("specname")

    def __repr__(self) -> str:
        return f"<HookImpl plugin_name={self.plugin_name!r}, plugin={self.plugin!r}>"


class HookCaller:
    """A hook caller that manages hook implementations and calls them."""

    __slots__ = ("name", "_hookimpls", "_hookspec", "_call_history", "_manager")

    def __init__(
        self,
        name: str,
        hookimpls: list[HookImpl] | None = None,
        hookspec: HookSpec | None = None,
        call_history: list[tuple[Mapping[str, object], Callable[[object], None] | None]] | None = None,
        _manager: Any = None,
    ) -> None:
        self.name: Final[str] = name
        self._hookimpls: list[HookImpl] = hookimpls if hookimpls is not None else []
        self._hookspec: HookSpec | None = hookspec
        self._call_history: list[tuple[Mapping[str, object], Callable[[object], None] | None]] | None = (
            call_history
        )
        self._manager = _manager

    def has_spec(self) -> bool:
        return self._hookspec is not None

    @property
    def spec(self) -> HookSpec | None:
        return self._hookspec

    def set_specification(self, spec: HookSpec) -> None:
        """Set the hook specification for this caller."""
        self._hookspec = spec
        if spec.opts.get("historic"):
            self._call_history = []

    def is_historic(self) -> bool:
        return self._call_history is not None

    def get_hookimpls(self) -> list[HookImpl]:
        """Return a copy of the list of hookimpls."""
        return self._hookimpls.copy()

    def _add_hookimpl(self, hookimpl: HookImpl) -> None:
        """Add a hookimpl to the caller.

        Hookimpls are ordered as follows:
        - Non-wrappers before wrappers
        - Among non-wrappers: trylast at front, tryfirst at back
        - Among wrappers: trylast at front, tryfirst at back
        """
        is_wrapper = hookimpl.hookwrapper or hookimpl.wrapper

        if not is_wrapper:
            # Find the index where wrappers start
            wrapper_idx = 0
            for i, impl in enumerate(self._hookimpls):
                if impl.hookwrapper or impl.wrapper:
                    wrapper_idx = i
                    break
            else:
                wrapper_idx = len(self._hookimpls)

            if hookimpl.trylast:
                # Insert at the beginning (among non-wrappers)
                self._hookimpls.insert(0, hookimpl)
            elif hookimpl.tryfirst:
                # Insert just before wrappers
                self._hookimpls.insert(wrapper_idx, hookimpl)
            else:
                # Insert somewhere in the middle of non-wrappers
                # Find the position after trylast but before tryfirst
                insert_idx = 0
                for i in range(wrapper_idx):
                    impl = self._hookimpls[i]
                    if not impl.trylast:
                        insert_idx = i
                        break
                else:
                    insert_idx = wrapper_idx

                # Now find position before tryfirst items
                for i in range(insert_idx, wrapper_idx):
                    if self._hookimpls[i].tryfirst:
                        insert_idx = i
                        break
                else:
                    insert_idx = wrapper_idx
                self._hookimpls.insert(insert_idx, hookimpl)
        else:
            # It's a wrapper
            if hookimpl.trylast:
                # Find the first wrapper and insert before it
                for i, impl in enumerate(self._hookimpls):
                    if impl.hookwrapper or impl.wrapper:
                        self._hookimpls.insert(i, hookimpl)
                        break
                else:
                    self._hookimpls.append(hookimpl)
            elif hookimpl.tryfirst:
                # Insert at the end
                self._hookimpls.append(hookimpl)
            else:
                # Insert after non-tryfirst wrappers
                # Find insertion point: after all trylast wrappers, before all tryfirst wrappers
                wrapper_start = len(self._hookimpls)
                for i, impl in enumerate(self._hookimpls):
                    if impl.hookwrapper or impl.wrapper:
                        wrapper_start = i
                        break

                # Find where tryfirst wrappers start
                insert_idx = len(self._hookimpls)
                for i in range(wrapper_start, len(self._hookimpls)):
                    if self._hookimpls[i].tryfirst:
                        insert_idx = i
                        break

                self._hookimpls.insert(insert_idx, hookimpl)

    def _remove_hookimpl(self, hookimpl: HookImpl) -> None:
        """Remove a hookimpl from the caller."""
        self._hookimpls.remove(hookimpl)

    def __repr__(self) -> str:
        return f"<HookCaller {self.name!r}>"

    def __call__(self, **kwargs: object) -> Any:
        """Call the hook with the given keyword arguments."""
        # Verify all spec args are provided (warn if not)
        self._verify_all_args_are_provided(kwargs)

        firstresult = self._hookspec.opts.get("firstresult", False) if self._hookspec else False

        if self._manager is not None:
            # Use the manager's _hookexec (for tracing/monitoring)
            return self._manager._hookexec(
                self.name,
                self._hookimpls.copy(),
                kwargs,
                firstresult,
            )
        else:
            # Direct call (no manager)
            from pluggy._callers import _multicall

            return _multicall(
                self.name,
                self._hookimpls.copy(),
                kwargs,
                firstresult,
            )

    def call_historic(
        self,
        result_callback: Callable[[object], None] | None = None,
        kwargs: Mapping[str, object] | None = None,
    ) -> None:
        """Call the hook historically.

        This means the call is recorded and new hookimpls will receive it.
        """
        if kwargs is None:
            kwargs = {}

        # Check that all spec arguments are provided
        self._verify_all_args_are_provided(kwargs)

        assert self._call_history is not None
        self._call_history.append((kwargs, result_callback))

        # Call all existing hookimpls
        firstresult = self._hookspec.opts.get("firstresult", False) if self._hookspec else False

        if self._manager is not None:
            results = self._manager._hookexec(
                self.name,
                self._hookimpls.copy(),
                kwargs,
                firstresult,
            )
        else:
            from pluggy._callers import _multicall
            results = _multicall(
                self.name,
                self._hookimpls.copy(),
                kwargs,
                firstresult,
            )
        if result_callback:
            if isinstance(results, list):
                for result in results:
                    result_callback(result)
            elif results is not None:
                result_callback(results)

    def call_extra(
        self,
        methods: Sequence[Callable[..., object]],
        kwargs: Mapping[str, object],
    ) -> Any:
        """Call the hook with extra one-off methods."""
        # Verify spec arguments
        self._verify_all_args_are_provided(kwargs)

        from pluggy._callers import _multicall

        # Create temporary HookImpls for the extra methods
        extra_hookimpls = []
        for method in methods:
            # Get the project name from the spec
            impl = HookImpl(
                None,
                "<extra>",
                method,
                {},
            )
            extra_hookimpls.append(impl)

        # Insert extra hookimpls after any tryfirst regular impls but before wrappers
        combined = self._hookimpls.copy()

        # Find the insertion point: after trylast, among regular (not tryfirst),
        # before wrappers
        insert_idx = 0
        for i, impl in enumerate(combined):
            if impl.hookwrapper or impl.wrapper:
                insert_idx = i
                break
            elif impl.tryfirst:
                insert_idx = i
                break
            else:
                insert_idx = i + 1
        else:
            insert_idx = len(combined)

        for extra in extra_hookimpls:
            combined.insert(insert_idx, extra)
            insert_idx += 1

        firstresult = self._hookspec.opts.get("firstresult", False) if self._hookspec else False

        if self._manager is not None:
            return self._manager._hookexec(
                self.name,
                combined,
                kwargs,
                firstresult,
            )
        else:
            return _multicall(
                self.name,
                combined,
                kwargs,
                firstresult,
            )

    def _verify_all_args_are_provided(self, kwargs: Mapping[str, object]) -> None:
        """Verify that all hook spec arguments are provided, issue warning if not."""
        if self._hookspec is None:
            return

        expected = set(self._hookspec.argnames)
        provided = set(kwargs.keys())
        missing = expected - provided

        if missing:
            missing_names = ", ".join(repr(a) for a in sorted(missing))
            warnings.warn(
                f"Argument(s) {missing_names} which are declared in the hookspec "
                f"cannot be found in this hook call",
                stacklevel=3,
            )

    def _maybe_apply_history(self, hookimpl: HookImpl) -> None:
        """Apply historic calls to a newly registered hookimpl."""
        if self._call_history:
            firstresult = self._hookspec.opts.get("firstresult", False) if self._hookspec else False

            for kwargs, result_callback in self._call_history:
                if self._manager is not None:
                    res = self._manager._hookexec(
                        self.name,
                        [hookimpl],
                        kwargs,
                        firstresult,
                    )
                else:
                    from pluggy._callers import _multicall
                    res = _multicall(
                        self.name,
                        [hookimpl],
                        kwargs,
                        firstresult,
                    )
                if result_callback:
                    if isinstance(res, list):
                        for r in res:
                            result_callback(r)
                    elif res is not None:
                        result_callback(res)

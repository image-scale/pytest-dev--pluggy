"""PluginManager - the core of pluggy."""
from __future__ import annotations

import inspect
import sys
import warnings
import importlib.metadata
from typing import Any
from typing import Callable
from typing import Final
from typing import Iterable
from typing import Mapping
from typing import Sequence

from pluggy._callers import HookCallError
from pluggy._hooks import HookCaller
from pluggy._hooks import HookImpl
from pluggy._hooks import HookimplOpts
from pluggy._hooks import HookSpec
from pluggy._hooks import HookspecMarker
from pluggy._hooks import HookspecOpts
from pluggy._hooks import varnames
from pluggy._result import Result
from pluggy._tracing import TagTracer


__all__ = ["PluginManager", "PluginValidationError", "_formatdef", "DistFacade"]


class PluginValidationError(Exception):
    """Error raised when a plugin fails validation."""

    def __init__(self, plugin: object, message: str) -> None:
        super().__init__(message)
        self.plugin = plugin


def _formatdef(func: Callable[..., object]) -> str:
    """Format function definition as a string."""
    return f"{func.__name__}{inspect.signature(func)}"


class DistFacade:
    """A simple Distribution facade for setuptools entrypoints."""

    def __init__(self, dist: importlib.metadata.Distribution) -> None:
        self._dist = dist

    @property
    def project_name(self) -> str:
        return self.metadata["name"]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._dist, name)

    def __dir__(self) -> list[str]:
        return sorted(set(dir(self._dist)) | {"_dist", "project_name"})


class _HookRelay:
    """A namespace for hook callers that can be accessed as attributes.

    Hook callers are created lazily when first accessed through getattr.
    Use _get_or_create_hook to explicitly create hooks for registration.
    """

    def __init__(self, manager: PluginManager) -> None:
        object.__setattr__(self, "_manager", manager)
        object.__setattr__(self, "_hooks", {})

    def _get_or_create_hook(self, name: str) -> HookCaller:
        """Get an existing hook or create a new one."""
        hooks = object.__getattribute__(self, "_hooks")
        manager = object.__getattribute__(self, "_manager")
        if name not in hooks:
            hooks[name] = HookCaller(name, _manager=manager)
        return hooks[name]

    def __getattr__(self, name: str) -> HookCaller:
        # Return existing hook or raise AttributeError
        hooks = object.__getattribute__(self, "_hooks")
        if name in hooks:
            return hooks[name]
        raise AttributeError(name)

    def __contains__(self, name: str) -> bool:
        """Check if a hook is registered."""
        hooks = object.__getattribute__(self, "_hooks")
        return name in hooks

    def __dir__(self) -> list[str]:
        """Return list of registered hook names."""
        hooks = object.__getattribute__(self, "_hooks")
        return list(hooks.keys())


class _SubsetHookCaller(HookCaller):
    """A hook caller that calls a subset of hookimpls."""

    __slots__ = ("_parent", "_removed_plugins")

    def __init__(
        self,
        parent: HookCaller,
        removed_plugins: Sequence[object],
    ) -> None:
        # Copy relevant attributes from parent
        self.name = parent.name  # type: ignore[misc]
        self._hookspec = parent._hookspec
        self._call_history = parent._call_history
        self._manager = parent._manager
        self._parent = parent
        self._removed_plugins = set(removed_plugins)
        # We don't copy _hookimpls - we filter from parent dynamically

    @property
    def _hookimpls(self) -> list[HookImpl]:  # type: ignore[override]
        return [
            impl
            for impl in self._parent._hookimpls
            if impl.plugin not in self._removed_plugins
        ]

    @_hookimpls.setter
    def _hookimpls(self, value: list[HookImpl]) -> None:
        # We don't store hookimpls directly - they come from parent
        pass

    def __repr__(self) -> str:
        return f"<_SubsetHookCaller {self.name!r}>"


class PluginManager:
    """Core plugin manager class.

    You use a PluginManager instance to register plugins,
    manage hookspecs and hookimpls, and call hooks.
    """

    def __init__(self, project_name: str) -> None:
        self.project_name: Final[str] = project_name
        self._name2plugin: dict[str, object] = {}
        self._plugin2hookcallers: dict[object, list[HookCaller]] = {}
        self._plugin_distinfo: list[tuple[object, DistFacade]] = []
        self.trace: TagTracer = TagTracer()
        self.hook: _HookRelay = _HookRelay(self)
        self._blocked: set[str] = set()

    def _hookexec(
        self,
        hook_name: str,
        methods: Sequence[HookImpl],
        kwargs: Mapping[str, object],
        firstresult: bool,
    ) -> object | list[object]:
        """Execute a hook call. Can be overridden for tracing."""
        from pluggy._callers import _multicall

        return _multicall(hook_name, methods, kwargs, firstresult)

    def register(
        self,
        plugin: object,
        name: str | None = None,
    ) -> str | None:
        """Register a plugin.

        Returns the plugin name or None if blocked.
        """
        if name is None:
            name = getattr(plugin, "__name__", None)
            if name is None:
                name = str(id(plugin))

        if name in self._blocked:
            return None

        if plugin in self._name2plugin.values():
            if name in self._name2plugin:
                if self._name2plugin[name] is plugin:
                    raise ValueError(f"Plugin already registered: {plugin!r}")
            raise ValueError(f"Plugin already registered: {plugin!r}")

        if name in self._name2plugin:
            raise ValueError(f"Plugin name already registered: {name!r}")

        self._name2plugin[name] = plugin

        # Find and register hook implementations
        hookcallers: list[HookCaller] = []

        for attr_name in dir(plugin):
            try:
                method = getattr(plugin, attr_name)
            except Exception:
                continue

            # Check for hookimpl marker using parse_hookimpl_opts
            # This allows subclasses to customize hook detection
            hookimpl_opts = self.parse_hookimpl_opts(plugin, attr_name)
            if hookimpl_opts is not None:
                # Determine which hook to register to
                hook_name = hookimpl_opts.get("specname") or attr_name

                # Ensure the hook exists (create if needed)
                hook = self.hook._get_or_create_hook(hook_name)

                # Create hookimpl
                hookimpl = HookImpl(plugin, name, method, hookimpl_opts)

                # Validate
                self._verify_hook(hook, hookimpl, [])

                # Add to hook
                hook._add_hookimpl(hookimpl)

                # Track which hooks this plugin is in
                if hook not in hookcallers:
                    hookcallers.append(hook)

                # Apply historical calls if historic hook
                if hook.is_historic():
                    hook._maybe_apply_history(hookimpl)

        self._plugin2hookcallers[plugin] = hookcallers
        return name

    def _verify_hook(
        self,
        hook: HookCaller,
        hookimpl: HookImpl,
        known_marks: list[str],
    ) -> None:
        """Verify that a hookimpl is valid for the given hook."""
        if hook.spec is not None:
            spec = hook.spec
            # Check for incompatible options
            if hookimpl.wrapper and hookimpl.hookwrapper:
                raise PluginValidationError(
                    hookimpl.plugin,
                    f"hookimpl {hookimpl.function!r}: wrapper and hookwrapper "
                    "are mutually exclusive"
                )

            # Check hookwrapper is a generator
            if hookimpl.hookwrapper:
                if not inspect.isgeneratorfunction(hookimpl.function):
                    raise PluginValidationError(
                        hookimpl.plugin,
                        f"hookimpl {hookimpl.function!r}: hookwrapper must be "
                        "a generator function"
                    )

            # Check wrapper with historic
            if spec.opts.get("historic"):
                if hookimpl.hookwrapper:
                    raise PluginValidationError(
                        hookimpl.plugin,
                        f"hookimpl {hookimpl.function!r}: hookwrapper not supported "
                        "for historic hooks"
                    )
                if hookimpl.wrapper:
                    raise PluginValidationError(
                        hookimpl.plugin,
                        f"hookimpl {hookimpl.function!r}: wrapper not supported "
                        "for historic hooks"
                    )

            # Check argument names
            notinspec = set(hookimpl.argnames) - set(spec.argnames) - set(spec.kwargnames)
            if notinspec:
                raise PluginValidationError(
                    hookimpl.plugin,
                    f"hookimpl {hookimpl.function!r}: argument(s) {notinspec} "
                    f"are declared in the hookimpl but cannot be found in the hookspec"
                )

            # Issue warnings if needed
            spec.warn_on_impl(hookimpl)
        else:
            # No spec yet - check basic validity
            if hookimpl.wrapper and hookimpl.hookwrapper:
                raise PluginValidationError(
                    hookimpl.plugin,
                    f"hookimpl {hookimpl.function!r}: wrapper and hookwrapper "
                    "are mutually exclusive"
                )

            if hookimpl.hookwrapper:
                if not inspect.isgeneratorfunction(hookimpl.function):
                    raise PluginValidationError(
                        hookimpl.plugin,
                        f"hookimpl {hookimpl.function!r}: hookwrapper must be "
                        "a generator function"
                    )

    def unregister(
        self,
        plugin: object | None = None,
        name: str | None = None,
    ) -> object | None:
        """Unregister a plugin."""
        if plugin is None and name is None:
            raise ValueError("one of plugin or name is required")

        if name is not None and name in self._blocked:
            return None

        if plugin is None:
            assert name is not None
            plugin = self._name2plugin.get(name)
            if plugin is None:
                return None

        if name is None:
            for n, p in self._name2plugin.items():
                if p is plugin:
                    name = n
                    break

        assert plugin is not None

        if plugin not in self._plugin2hookcallers:
            assert False, f"Plugin {plugin!r} not registered"

        # Remove from all hooks
        hookcallers = self._plugin2hookcallers.get(plugin, [])
        for hook in hookcallers:
            # Find and remove all hookimpls for this plugin
            impls_to_remove = [
                impl for impl in hook._hookimpls if impl.plugin is plugin
            ]
            for impl in impls_to_remove:
                hook._remove_hookimpl(impl)

        # Remove from tracking
        del self._plugin2hookcallers[plugin]
        if name is not None:
            del self._name2plugin[name]

        return plugin

    def set_blocked(self, name: str) -> None:
        """Block a plugin name from being registered."""
        # Unregister first if already registered (before blocking)
        if name in self._name2plugin:
            self.unregister(name=name)
        self._blocked.add(name)

    def is_blocked(self, name: str) -> bool:
        """Check if a plugin name is blocked."""
        return name in self._blocked

    def unblock(self, name: str) -> bool:
        """Unblock a plugin name.

        Returns True if the name was blocked, False otherwise.
        """
        if name in self._blocked:
            self._blocked.remove(name)
            return True
        return False

    def add_hookspecs(self, module_or_class: object) -> None:
        """Add hook specifications from a module or class."""
        names: list[str] = []
        for name in dir(module_or_class):
            if name.startswith("_"):
                continue

            method = getattr(module_or_class, name, None)
            if method is None:
                continue

            # Use parse_hookspec_opts to allow customization by subclasses
            spec_opts = self.parse_hookspec_opts(module_or_class, name)
            if spec_opts is not None:
                names.append(name)
                hook = self.hook._get_or_create_hook(name)

                # Check for conflict
                if hook.has_spec():
                    raise ValueError(
                        f"Hook {name!r} is already registered within namespace "
                        f"{hook.spec.namespace!r}"  # type: ignore[union-attr]
                    )

                # Create and set the spec
                spec = HookSpec(module_or_class, name, method, spec_opts)
                hook.set_specification(spec)

                # Validate existing hookimpls against the new spec
                for hookimpl in hook.get_hookimpls():
                    self._verify_hook(hook, hookimpl, [])

        if not names:
            raise ValueError(f"did not find any {self.project_name!r} hooks in {module_or_class!r}")

    def get_plugins(self) -> set[object]:
        """Return the set of registered plugins."""
        return set(self._name2plugin.values())

    def is_registered(self, plugin: object) -> bool:
        """Check if a plugin is registered."""
        return plugin in self._plugin2hookcallers

    def get_plugin(self, name: str) -> object | None:
        """Get a plugin by name."""
        return self._name2plugin.get(name)

    def has_plugin(self, name: str) -> bool:
        """Check if a plugin with the given name is registered."""
        return name in self._name2plugin

    def get_hookcallers(self, plugin: object) -> list[HookCaller] | None:
        """Get the list of hook callers for a plugin."""
        if plugin not in self._plugin2hookcallers:
            return None
        hookcallers = self._plugin2hookcallers[plugin]
        # Remove duplicates (a plugin may have multiple impls on the same hook)
        seen: set[str] = set()
        unique: list[HookCaller] = []
        for hc in hookcallers:
            if hc.name not in seen:
                seen.add(hc.name)
                unique.append(hc)
        return unique

    def list_name_plugin(self) -> list[tuple[str, object]]:
        """Return a list of (name, plugin) pairs."""
        return list(self._name2plugin.items())

    def list_plugin_distinfo(self) -> list[tuple[object, DistFacade]]:
        """Return a list of (plugin, distinfo) pairs."""
        return list(self._plugin_distinfo)

    def load_setuptools_entrypoints(
        self,
        group: str,
        name: str | None = None,
    ) -> int:
        """Load plugins from setuptools entry points.

        Returns the number of plugins loaded.
        """
        count = 0
        for dist in importlib.metadata.distributions():
            for ep in dist.entry_points:
                if ep.group != group:
                    continue
                if name is not None and ep.name != name:
                    continue
                if self.get_plugin(ep.name) is not None:
                    continue
                if self.is_blocked(ep.name):
                    continue

                plugin = ep.load()
                self.register(plugin, name=ep.name)
                self._plugin_distinfo.append((plugin, DistFacade(dist)))
                count += 1
        return count

    def check_pending(self) -> None:
        """Check for unknown hooks (hookimpls without corresponding hookspecs)."""
        for name in dir(self.hook):
            if name.startswith("_"):
                continue
            if name not in self.hook:
                continue
            hook = self.hook._get_or_create_hook(name)
            if not isinstance(hook, HookCaller):
                continue
            if hook.spec is None:
                for hookimpl in hook.get_hookimpls():
                    if not hookimpl.optionalhook:
                        raise PluginValidationError(
                            hookimpl.plugin,
                            f"unknown hook {name!r} in plugin {hookimpl.plugin!r}"
                        )

    def add_hookcall_monitoring(
        self,
        before: Callable[[str, list[HookImpl], Mapping[str, object]], None],
        after: Callable[[object | list[object], str, list[HookImpl], Mapping[str, object]], None],
    ) -> Callable[[], None]:
        """Add monitoring callbacks for hook calls.

        Returns a function to remove the monitoring.
        """
        oldcall = self._hookexec

        def traced_hookexec(
            hook_name: str,
            hook_impls: Sequence[HookImpl],
            kwargs: Mapping[str, object],
            firstresult: bool,
        ) -> object | list[object]:
            before(hook_name, list(hook_impls), kwargs)
            outcome = Result.from_call(
                lambda: oldcall(hook_name, hook_impls, kwargs, firstresult)
            )
            after(outcome, hook_name, list(hook_impls), kwargs)
            return outcome.get_result()

        self._hookexec = traced_hookexec  # type: ignore[method-assign]

        def undo() -> None:
            self._hookexec = oldcall  # type: ignore[method-assign]

        return undo

    def enable_tracing(self) -> Callable[[], None]:
        """Enable hook call tracing.

        Returns a function to disable tracing.
        """
        hooktrace = self.trace.get("hook")

        def before(
            hook_name: str,
            methods: list[HookImpl],
            kwargs: Mapping[str, object],
        ) -> None:
            hooktrace.root.indent += 1
            hooktrace(hook_name, kwargs)

        def after(
            outcome: object | list[object],
            hook_name: str,
            methods: list[HookImpl],
            kwargs: Mapping[str, object],
        ) -> None:
            # Extract result from outcome if it's a Result
            if isinstance(outcome, Result):
                try:
                    res = outcome.get_result()
                except BaseException as e:
                    res = None
            else:
                res = outcome
            hooktrace("finish", hook_name, "-->", res)
            hooktrace.root.indent -= 1

        return self.add_hookcall_monitoring(before, after)

    def subset_hook_caller(
        self,
        name: str,
        remove_plugins: Sequence[object],
    ) -> HookCaller:
        """Return a subset HookCaller that excludes certain plugins."""
        hook = self.hook._get_or_create_hook(name)
        return _SubsetHookCaller(hook, remove_plugins)

    def parse_hookimpl_opts(
        self,
        module_or_class: object,
        name: str,
    ) -> HookimplOpts | None:
        """Parse hookimpl options from a method.

        Can be overridden to change how hookimpls are detected.
        """
        try:
            method = getattr(module_or_class, name, None)
        except Exception:
            return None
        if method is None:
            return None
        try:
            return getattr(method, self.project_name + "_impl", None)
        except Exception:
            return None

    def parse_hookspec_opts(
        self,
        module_or_class: object,
        name: str,
    ) -> HookspecOpts | None:
        """Parse hookspec options from a method.

        Can be overridden to change how hookspecs are detected.
        """
        method = getattr(module_or_class, name, None)
        if method is None:
            return None
        return getattr(method, self.project_name + "_spec", None)

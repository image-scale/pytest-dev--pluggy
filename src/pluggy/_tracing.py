"""Tracing support for hook call debugging."""
from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Mapping
from typing import Sequence
from typing import Tuple

__all__ = ["TagTracer", "TagTracerSub"]


class TagTracerSub:
    """A sub-tracer tied to specific tags."""

    def __init__(self, root: TagTracer, tags: tuple[str, ...]) -> None:
        self.root = root
        self.tags = tags

    def __call__(self, *args: object) -> None:
        self.root._processmessage(self.tags, args)

    def get(self, name: str) -> TagTracerSub:
        return TagTracerSub(self.root, self.tags + (name,))


class TagTracer:
    """A hierarchical tag-based tracer for debugging hook calls."""

    def __init__(self) -> None:
        self._tags2proc: dict[tuple[str, ...], Callable[..., object]] = {}
        self._writer: Callable[[str], object] | None = None
        self.indent: int = 0
        self.root = self  # Self-reference for compatibility

    def __call__(self, *args: object) -> None:
        """Process a trace message with no tags (when called directly)."""
        self._processmessage((), args)

    def get(self, name: str) -> TagTracerSub:
        return TagTracerSub(self, (name,))

    def _format_message(self, tags: Sequence[str], args: Sequence[object]) -> str:
        """Format a message with indentation and tags."""
        if args:
            # Check if the last arg is a dict for special formatting
            if len(args) >= 2 and isinstance(args[-1], Mapping):
                main_args = args[:-1]
                extra = args[-1]
                lines = ["  " * self.indent + " ".join(str(x) for x in main_args)]
                lines[0] += " [" + ":".join(tags) + "]\n"
                for key, value in extra.items():
                    lines.append(f"    {key}: {value}\n")
                return "".join(lines)
            else:
                main_line = "  " * self.indent + " ".join(str(x) for x in args)
        else:
            main_line = "  " * self.indent
        return main_line + " [" + ":".join(tags) + "]\n"

    def _processmessage(self, tags: tuple[str, ...], args: Sequence[object]) -> None:
        """Process a trace message."""
        # Check for processor
        if tags in self._tags2proc:
            self._tags2proc[tags](tags, args)
            return

        # Otherwise, write to writer if present
        if self._writer is not None:
            self._writer(self._format_message(tags, args))

    def setwriter(self, writer: Callable[[str], object] | None) -> None:
        """Set the trace output writer."""
        self._writer = writer

    def setprocessor(
        self,
        tags: str | tuple[str, ...],
        processor: Callable[[tuple[str, ...], tuple[object, ...]], object],
    ) -> None:
        """Set a processor for specific tags."""
        if isinstance(tags, str):
            tags = tuple(tags.split(":"))
        self._tags2proc[tags] = processor

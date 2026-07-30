"""ToolRegistry: name → Tool dispatch with lifecycle hooks and automatic tracing."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..logging_utils import log_event, new_trace_id
from .base import Tool, ToolResult
from .hooks import HookRunner

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Holds named Tool instances and dispatches execute() calls.

    Every dispatch fires before/after hooks and emits structured
    ``tool.invoke.{start,done,fail}`` log events. The registry never raises
    from ``execute()`` — exceptions are converted to ``ToolResult(error=...)``
    so callers can keep going on partial failure.
    """

    def __init__(self, hook_runner: HookRunner | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._hooks = hook_runner or HookRunner()

    @property
    def hooks(self) -> HookRunner:
        return self._hooks

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning("Tool %r is already registered; overwriting.", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(
                f"Tool {name!r} is not registered. Available: {sorted(self._tools)}"
            ) from exc

    def list(self) -> list[str]:
        return sorted(self._tools.keys())

    def execute(self, name: str, /, **inputs: Any) -> ToolResult:
        tool = self.get(name)
        trace_id = new_trace_id("tool")
        started = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "tool.invoke.start",
            component="tool",
            tool=tool.name,
            trace_id=trace_id,
            input_keys=sorted(inputs.keys()),
        )

        self._hooks.fire_before(tool, inputs)

        try:
            result = tool.execute(**inputs)
            if not isinstance(result, ToolResult):
                result = ToolResult(
                    error=f"tool {tool.name!r} returned {type(result).__name__}, expected ToolResult"
                )
        except Exception as exc:  # noqa: BLE001  registry-level boundary catch
            result = ToolResult(error=f"{type(exc).__name__}: {exc}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            logger,
            logging.INFO if result.ok else logging.WARNING,
            "tool.invoke.done" if result.ok else "tool.invoke.fail",
            component="tool",
            tool=tool.name,
            trace_id=trace_id,
            elapsed_ms=elapsed_ms,
            doc_count=len(result.documents),
            error=result.error,
        )

        result.meta.setdefault("trace_id", trace_id)
        result.meta.setdefault("elapsed_ms", elapsed_ms)

        self._hooks.fire_after(tool, inputs, result)
        return result


_default_registry: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """Lazy-singleton registry seeded with the project's built-in retrieval tools.

    First call constructs the registry and registers the retrieval tools.
    Subsequent calls return the same
    instance.
    """
    global _default_registry
    if _default_registry is None:
        registry = ToolRegistry()
        # Local import to avoid circular: retrieval_tools imports from base/hooks.
        from .retrieval_tools import register_default_retrieval_tools

        register_default_retrieval_tools(registry)
        _default_registry = registry
    return _default_registry

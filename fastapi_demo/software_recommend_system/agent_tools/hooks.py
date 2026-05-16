"""Lifecycle hook seams: before_tool_call and after_tool_call.

Hooks let plugins observe (and later veto) every tool invocation without
modifying the registry or individual tools. This is the smallest viable
version of the pattern; permission checks and caching can layer on top
later without changing the surface area.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .base import Tool, ToolResult

logger = logging.getLogger(__name__)

BeforeHook = Callable[[Tool, dict[str, Any]], None]
AfterHook = Callable[[Tool, dict[str, Any], ToolResult], None]


class HookRunner:
    """Holds registered before/after hooks and fires them sequentially.

    Hook errors are logged and swallowed (fail-open) so an
    instrumentation bug never blocks a real tool call.
    """

    def __init__(self) -> None:
        self._before: list[BeforeHook] = []
        self._after: list[AfterHook] = []

    def add_before(self, hook: BeforeHook) -> None:
        self._before.append(hook)

    def add_after(self, hook: AfterHook) -> None:
        self._after.append(hook)

    def fire_before(self, tool: Tool, inputs: dict[str, Any]) -> None:
        for hook in self._before:
            try:
                hook(tool, inputs)
            except Exception as exc:
                logger.warning(
                    "before_tool_call hook %r failed on tool=%s: %s",
                    getattr(hook, "__name__", hook),
                    tool.name,
                    exc,
                )

    def fire_after(self, tool: Tool, inputs: dict[str, Any], result: ToolResult) -> None:
        for hook in self._after:
            try:
                hook(tool, inputs, result)
            except Exception as exc:
                logger.warning(
                    "after_tool_call hook %r failed on tool=%s: %s",
                    getattr(hook, "__name__", hook),
                    tool.name,
                    exc,
                )

    @property
    def before_count(self) -> int:
        return len(self._before)

    @property
    def after_count(self) -> int:
        return len(self._after)

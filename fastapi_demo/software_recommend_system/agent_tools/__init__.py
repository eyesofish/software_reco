"""Agent-tool abstractions for software_reco.

This package introduces a uniform Tool interface, a registry that dispatches
calls and fires lifecycle hooks, and wrappers around the four existing
retrieval channels (vector/keyword/web/memory) so a future LLM-driven agent
loop can invoke them through one entry point.

See: ``base`` (Tool / ToolResult), ``registry`` (ToolRegistry, default_registry),
``hooks`` (HookRunner), ``retrieval_tools`` (built-in retrieval wrappers).
"""

from .base import Tool, ToolResult
from .hooks import HookRunner
from .registry import ToolRegistry, default_registry

__all__ = [
    "Tool",
    "ToolResult",
    "HookRunner",
    "ToolRegistry",
    "default_registry",
]

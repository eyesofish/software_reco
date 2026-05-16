"""Tool interface and result envelope."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..document_schema import Document


@dataclass
class ToolResult:
    """Uniform return type for every Tool.execute() call.

    The registry guarantees a ToolResult is always returned (never raises) —
    so callers can treat tool failures as partial-success conditions without
    wrapping every call in try/except.
    """

    documents: list[Document] = field(default_factory=list)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class Tool(ABC):
    """A single retrieval/action capability the agent can invoke.

    Subclasses declare ``name``, ``description`` and ``input_schema`` as class
    attributes (so LLMs can render them into a tool list) and implement
    ``execute()``. Tools are stateless and reused across requests.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    def execute(self, **inputs: Any) -> ToolResult:
        """Run the tool. Should raise on unexpected error — the registry
        catches and converts to ToolResult(error=...) for the caller."""

"""Tool wrappers for the four built-in retrieval channels.

Each tool is a thin adapter: it accepts JSON-schema-validated inputs from the
registry and delegates to the existing ``recall_*`` functions in
``retrieval_channels``. Behavior of the underlying retrievers is unchanged —
this is structural so an agent loop can call them through one interface.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult


class VectorTool(Tool):
    name = "vector"
    description = "Dense vector similarity search over the local Chroma index."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "default": 8},
            "trace_id": {"type": "string", "nullable": True},
        },
        "required": ["query"],
    }

    def execute(
        self, *, query: str, top_k: int = 8, trace_id: str | None = None, **_: Any
    ) -> ToolResult:
        from ..retrieval_channels import recall_vector

        return ToolResult(documents=recall_vector(query, top_k, trace_id))


class WebTool(Tool):
    name = "web"
    description = "External web search (Tavily) for fresh / out-of-corpus evidence."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "default": 3},
            "trace_id": {"type": "string", "nullable": True},
        },
        "required": ["query"],
    }

    def execute(
        self, *, query: str, top_k: int = 3, trace_id: str | None = None, **_: Any
    ) -> ToolResult:
        from ..retrieval_channels import recall_web

        return ToolResult(documents=recall_web(query, top_k, trace_id))


class KeywordTool(Tool):
    name = "keyword"
    description = "BM25 keyword recall against the local Chroma 'software_recommendations' collection."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "default": 15},
            "trace_id": {"type": "string", "nullable": True},
        },
        "required": ["query"],
    }

    def execute(
        self, *, query: str, top_k: int = 15, trace_id: str | None = None, **_: Any
    ) -> ToolResult:
        from ..retrieval_channels import recall_keyword

        return ToolResult(documents=recall_keyword(query, top_k, trace_id))


class MemoryTool(Tool):
    name = "memory"
    description = "Layered memory recall (working / episodic / semantic / fact) scored by overlap and salience."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "session_id": {"type": "string", "nullable": True},
            "top_k": {"type": "integer", "minimum": 1, "default": 6},
            "memory_context": {"type": "array", "items": {"type": "object"}, "nullable": True},
        },
        "required": ["query"],
    }

    def execute(
        self,
        *,
        query: str,
        session_id: str | None = None,
        top_k: int = 6,
        memory_context: list[dict] | None = None,
        **_: Any,
    ) -> ToolResult:
        from ..retrieval_channels import recall_memory

        return ToolResult(
            documents=recall_memory(
                query,
                session_id=session_id,
                top_k=top_k,
                memory_context=memory_context,
            )
        )


def register_default_retrieval_tools(registry) -> None:
    """Register the four built-in retrieval tools on a registry."""
    registry.register(VectorTool())
    registry.register(WebTool())
    registry.register(KeywordTool())
    registry.register(MemoryTool())

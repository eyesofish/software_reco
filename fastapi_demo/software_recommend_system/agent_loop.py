"""LLM-driven tool-calling loop over the existing ToolRegistry.

Until now the registry was a seam: the tools existed and were dispatchable, but
`retrieve_node` still called the `recall_*` functions directly in a fixed order.
This module closes that gap — the model picks which retrieval tools to call and
with what arguments, and the registry executes them with its existing tracing and
hooks.

It is opt-in (`AGENT_LOOP_ENABLE`, default off) and always degradable: any
failure returns ``None`` so the caller keeps the deterministic static pipeline.
That keeps offline and no-API-key runs working exactly as before.

Bounded on purpose:

* ``AGENT_LOOP_MAX_STEPS`` caps model turns,
* ``AGENT_LOOP_MAX_TOOL_CALLS`` caps total tool executions,
* repeated identical calls are refused rather than executed — a model looping on
  the same tool with the same arguments is the classic failure mode here,
* tool output fed back into context is truncated so a large recall cannot blow up
  the prompt.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .agent_tools.registry import ToolRegistry, default_registry
from .config import settings
from .document_schema import Document
from .llm_governance import governed_chat_completion
from .logging_utils import log_event, new_trace_id

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a retrieval planner for a software engineering question answering system. "
    "Use the provided tools to gather evidence that answers the user's question. "
    "Prefer the local corpus tools (vector, keyword) first; use web only when the "
    "question needs fresh or out-of-corpus information, and memory only when the "
    "question refers to earlier conversation. "
    "Call tools with focused queries. Do not repeat a call with identical arguments. "
    "When you have enough evidence, reply with a short plain-text summary and no "
    "further tool calls."
)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_tool_specs(registry: ToolRegistry) -> list[dict[str, Any]]:
    """Render registered tools as OpenAI function-calling specs."""
    specs: list[dict[str, Any]] = []
    for name in registry.list():
        tool = registry.get(name)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
        )
    return specs


def _tool_calls_of(message: Any) -> list[Any]:
    return list(_get_field(message, "tool_calls", None) or [])


def _call_function(call: Any) -> tuple[str, str, str]:
    """Return (call_id, tool_name, raw_arguments) for one tool call."""
    call_id = str(_get_field(call, "id", "") or "")
    function = _get_field(call, "function", None)
    name = str(_get_field(function, "name", "") or "")
    arguments = _get_field(function, "arguments", "") or ""
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return call_id, name, arguments


def _summarize_documents(documents: list[Document], limit_chars: int) -> str:
    if not documents:
        return "No documents found."
    lines = []
    for index, doc in enumerate(documents, start=1):
        content = str(_get_field(doc, "content", "") or "").replace("\n", " ").strip()
        lines.append(f"[{index}] {content[:limit_chars]}")
    return "\n".join(lines)


def _assistant_message_payload(message: Any, calls: list[Any]) -> dict[str, Any]:
    """Rebuild the assistant turn so the follow-up request stays well-formed."""
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": str(_get_field(message, "content", "") or ""),
        "tool_calls": [],
    }
    for call in calls:
        call_id, name, arguments = _call_function(call)
        payload["tool_calls"].append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return payload


def run_agent_loop(
    question: str,
    *,
    registry: ToolRegistry | None = None,
    client: Any | None = None,
    session_id: str | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    top_k: int | None = None,
) -> list[Document] | None:
    """Let the model drive retrieval through the tool registry.

    Returns collected documents, or ``None`` when the loop cannot run or produced
    nothing, so the caller falls back to the static pipeline.
    """
    normalized_question = str(question or "").strip()
    if not normalized_question:
        return None

    model = str(
        getattr(settings, "AGENT_LOOP_MODEL", "") or getattr(settings, "LLM_MODEL", "")
    ).strip()
    if not model:
        return None

    registry = registry or default_registry()
    tool_specs = build_tool_specs(registry)
    if not tool_specs:
        return None

    if client is None:
        from .llm_utils import _get_openai_client

        client = _get_openai_client()

    max_steps = max(1, _safe_int(getattr(settings, "AGENT_LOOP_MAX_STEPS", 3), 3))
    max_tool_calls = max(1, _safe_int(getattr(settings, "AGENT_LOOP_MAX_TOOL_CALLS", 6), 6))
    result_chars = max(80, _safe_int(getattr(settings, "AGENT_LOOP_TOOL_RESULT_CHARS", 500), 500))
    max_docs = max(1, _safe_int(getattr(settings, "AGENT_LOOP_MAX_DOCS", 20), 20))

    trace_id = new_trace_id("agent")
    started_at = time.perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": normalized_question},
    ]

    collected: list[Document] = []
    seen_doc_ids: set[str] = set()
    executed_signatures: set[str] = set()
    total_calls = 0

    log_event(
        logger,
        logging.INFO,
        "agent.loop.start",
        component="agent",
        trace_id=trace_id,
        model=model,
        tools=registry.list(),
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )

    for step in range(1, max_steps + 1):
        try:
            response = governed_chat_completion(
                client=client,
                scene="agent_loop",
                model=model,
                messages=messages,
                tools=tool_specs,
                tool_choice="auto",
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001  loop is best-effort
            log_event(
                logger,
                logging.WARNING,
                "agent.loop.llm.fail",
                component="agent",
                trace_id=trace_id,
                step=step,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return collected or None

        choices = _get_field(response, "choices", []) or []
        if not choices:
            break
        message = _get_field(choices[0], "message", None)
        calls = _tool_calls_of(message)
        if not calls:
            log_event(
                logger,
                logging.INFO,
                "agent.loop.finish",
                component="agent",
                trace_id=trace_id,
                step=step,
                reason="model_stopped",
                docs=len(collected),
            )
            break

        messages.append(_assistant_message_payload(message, calls))

        for call in calls:
            call_id, name, raw_arguments = _call_function(call)

            if total_calls >= max_tool_calls:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "Tool call budget exhausted. Summarize with what you have.",
                    }
                )
                continue

            try:
                arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"Invalid arguments: {exc}. Retry with a JSON object.",
                    }
                )
                continue

            signature = f"{name}::{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
            if signature in executed_signatures:
                log_event(
                    logger,
                    logging.WARNING,
                    "agent.loop.tool.duplicate",
                    component="agent",
                    trace_id=trace_id,
                    step=step,
                    tool=name,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "Identical call already executed. Use different arguments or stop.",
                    }
                )
                continue
            executed_signatures.add(signature)

            # Session-scoped inputs come from the caller, not the model.
            if name == "memory":
                arguments["session_id"] = session_id
                arguments["memory_context"] = memory_context or []
            if top_k is not None and "top_k" not in arguments:
                arguments["top_k"] = top_k
            arguments.setdefault("trace_id", trace_id)

            total_calls += 1
            try:
                # ToolRegistry.execute() converts tool failures into ToolResult(error=...)
                # but still raises KeyError for an unregistered name, and an LLM will
                # eventually hallucinate one.
                tool_result = registry.execute(name, **arguments)
            except KeyError:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"Unknown tool {name!r}. Available: {registry.list()}",
                    }
                )
                continue
            except TypeError as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"Bad arguments for {name!r}: {exc}",
                    }
                )
                continue

            if not tool_result.ok:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"Tool {name!r} failed: {tool_result.error}",
                    }
                )
                continue

            new_docs: list[Document] = []
            for doc in tool_result.documents:
                metadata = _get_field(doc, "metadata", {}) or {}
                doc_id = str(_get_field(metadata, "doc_id", "") or "").strip()
                key = doc_id or str(_get_field(doc, "content", ""))[:120]
                if key in seen_doc_ids:
                    continue
                seen_doc_ids.add(key)
                new_docs.append(doc)
                if len(collected) + len(new_docs) >= max_docs:
                    break

            collected.extend(new_docs)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _summarize_documents(new_docs, result_chars),
                }
            )

        if len(collected) >= max_docs or total_calls >= max_tool_calls:
            log_event(
                logger,
                logging.INFO,
                "agent.loop.finish",
                component="agent",
                trace_id=trace_id,
                step=step,
                reason="budget_reached",
                docs=len(collected),
            )
            break

    log_event(
        logger,
        logging.INFO,
        "agent.loop.done",
        component="agent",
        trace_id=trace_id,
        model=model,
        tool_calls=total_calls,
        docs=len(collected),
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )
    return collected or None

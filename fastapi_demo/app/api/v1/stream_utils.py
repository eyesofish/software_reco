import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from langgraph.types import Command

from software_recommend_system.execution_control import run_controlled, stream_controlled
from software_recommend_system.observability import traceable

logger = logging.getLogger(__name__)


def _execution_budget(agent: Any, graph_input: Any, config: dict[str, Any]) -> tuple[float, int]:
    from software_recommend_system.execution_control import RunControl, RunStopped

    values = (
        graph_input
        if isinstance(graph_input, dict)
        else (graph_input.model_dump() if hasattr(graph_input, "model_dump") else {})
    )
    if isinstance(graph_input, Command):
        snapshot = agent.get_state(config)
        values = snapshot.values if isinstance(getattr(snapshot, "values", None), dict) else {}
        if values.get("execution_cancelled"):
            raise ValueError("Task has been cancelled")
    spent = int(values.get("active_elapsed_ms", 0))
    total = float(values.get("timeout_budget", 60))
    remaining = total - spent / 1000
    if remaining <= 0:
        control = RunControl(str(config.get("configurable", {}).get("thread_id", "")), 0)
        raise RunStopped("timeout", control)
    return remaining, spent


def _persist_execution_time(agent: Any, config: dict[str, Any], spent: int) -> None:
    """Include orchestration time; interrupted wall-clock waiting is never charged."""
    if not callable(getattr(agent, "get_state", None)) or not callable(getattr(agent, "update_state", None)):
        return
    snapshot = agent.get_state(config)
    values = getattr(snapshot, "values", {})
    if isinstance(values, dict) and values:
        updates: dict[str, Any] = {"active_elapsed_ms": max(spent, int(values.get("active_elapsed_ms", 0)))}
        # update_state preserves the next node but may clear its task-local
        # interrupt object. Persist the waiting payload before that happens.
        for task in getattr(snapshot, "tasks", ()):
            for item in getattr(task, "interrupts", ()):
                payload = getattr(item, "value", None)
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") == "awaiting_input":
                    updates["pending_input"] = payload.get("pending_input")
                    updates["awaiting_human_confirmation"] = False
                elif payload.get("type") == "human_confirmation":
                    updates["pending_input"] = None
                    updates["awaiting_human_confirmation"] = True
                    updates["human_confirmation_done"] = False
                    updates["pending_sub_questions"] = payload.get("sub_questions", [])
        agent.update_state(config, updates)


def _sse(event: str, payload: dict[str, Any] | None = None) -> str:
    body = dict(payload or {})
    body.setdefault("type", event)
    return f"event: {event}\ndata: {json.dumps(body, ensure_ascii=False, default=str)}\n\n"


def _to_stream_mode_chunk(item: Any) -> tuple[str, Any]:
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item[0], item[1]
    return "updates", item


def _iter_update_nodes(chunk: Any) -> list[tuple[str, Any]]:
    if not isinstance(chunk, dict):
        return []
    entries: list[tuple[str, Any]] = []
    for node_name, payload in chunk.items():
        node = str(node_name or "").strip()
        if not node or node.startswith("__"):
            continue
        entries.append((node, payload))
    return entries


def _iter_custom_events(chunk: Any) -> list[dict[str, Any]]:
    if isinstance(chunk, dict):
        return [chunk]
    if isinstance(chunk, (list, tuple)):
        return [item for item in chunk if isinstance(item, dict)]
    return []


def _merge_stream_updates(result: dict[str, Any], chunk: Any) -> None:
    if not isinstance(chunk, dict):
        return
    for node_name, payload in chunk.items():
        node = str(node_name or "").strip()
        if node == "__interrupt__":
            result["__interrupt__"] = payload
            continue
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            result[key] = value


def _is_sync_sqlite_checkpointer(checkpointer_type: str, checkpointer_module: str) -> bool:
    return (
        checkpointer_type == "SqliteSaver"
        and "langgraph.checkpoint.sqlite" in checkpointer_module
        and ".aio" not in checkpointer_module
    )


async def _stream_agent_sync_fallback(
    agent: Any,
    graph_input: Any,
    config: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
) -> AsyncIterator[Any]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def produce() -> None:
        try:
            iterator = agent.stream(
                graph_input,
                config=config,
                stream_mode=stream_mode,
            )
            for item in iterator:
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    producer_task = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await producer_task


@traceable(name="api_agent_stream")
async def run_agent_stream_async(
    agent: Any,
    graph_input: Any,
    *,
    config: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
) -> AsyncIterator[Any]:
    checkpointer = getattr(agent, "checkpointer", None)
    checkpointer_type = type(checkpointer).__name__
    checkpointer_module = getattr(type(checkpointer), "__module__", "")
    mode_list = list(stream_mode or ["updates", "custom"])

    session_id = str((config or {}).get("configurable", {}).get("thread_id", ""))
    timeout_seconds, prior_spent = await asyncio.to_thread(_execution_budget, agent, graph_input, config or {})
    if isinstance(graph_input, dict):
        session_id = str(graph_input.get("session_id", session_id) or session_id)
    else:
        session_id = str(getattr(graph_input, "session_id", session_id) or session_id)

    logger.info(
        "AGENT_STREAM_START mode=bounded_sync graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
        type(graph_input).__name__,
        checkpointer_type,
        checkpointer_module,
        mode_list,
    )
    async for item, _control in stream_controlled(
        lambda control: _stream_with_run_id(agent, graph_input, config, mode_list, control.run_id),
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    ):
        if item is None:
            await asyncio.to_thread(_persist_execution_time, agent, config or {}, prior_spent + _control.elapsed_ms)
            yield ("__execution__", {"run_id": _control.run_id, "elapsed_ms": _control.elapsed_ms})
        else:
            yield item


@traceable(name="api_agent_invoke")
async def run_agent_async(agent: Any, graph_input: Any, config: dict[str, Any] | None = None) -> Any:
    """Run one graph invocation inside the bounded, deadline-aware worker pool."""
    from .handlers.normalizers import _get_value

    config = dict(config or {})
    config["recursion_limit"] = 64
    session_id = str(config.get("configurable", {}).get("thread_id", ""))
    timeout_seconds, prior_spent = await asyncio.to_thread(_execution_budget, agent, graph_input, config)
    session_id = str(_get_value(graph_input, "session_id", session_id) or session_id)
    started_at = time.perf_counter()
    result, control = await run_controlled(
        lambda run: _invoke_with_run_id(agent, graph_input, config, run.run_id),
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    await asyncio.to_thread(_persist_execution_time, agent, config, prior_spent + control.elapsed_ms)
    logger.info(
        "AGENT_INVOKE_DONE run_id=%s elapsed_ms=%d",
        control.run_id,
        int((time.perf_counter() - started_at) * 1000),
    )
    if isinstance(result, dict):
        result["_execution"] = {"run_id": control.run_id, "elapsed_ms": control.elapsed_ms}
    return result


def _invoke_with_run_id(agent: Any, graph_input: Any, config: dict[str, Any], run_id: str) -> Any:
    if isinstance(graph_input, dict):
        graph_input["run_id"] = run_id
    elif hasattr(graph_input, "run_id"):
        graph_input.run_id = run_id
    if not isinstance(graph_input, Command) and hasattr(graph_input, "model_dump"):
        # Include explicit defaults on every new turn; Pydantic fields-set alone
        # could leave a cancelled flag or old routing decision in a shared thread.
        graph_input = graph_input.model_dump()
    return agent.invoke(graph_input, config=config)


def _stream_with_run_id(
    agent: Any, graph_input: Any, config: dict[str, Any] | None, stream_mode: list[str], run_id: str
) -> Any:
    if isinstance(graph_input, dict):
        graph_input["run_id"] = run_id
    elif hasattr(graph_input, "run_id"):
        graph_input.run_id = run_id
    if not isinstance(graph_input, Command) and hasattr(graph_input, "model_dump"):
        graph_input = graph_input.model_dump()
    return agent.stream(
        graph_input,
        config={**(config or {}), "recursion_limit": 64},
        stream_mode=stream_mode,
    )

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from software_recommend_system.observability import traceable

logger = logging.getLogger(__name__)


def _sse(event: str, payload: dict[str, Any] | None = None) -> str:
    body = dict(payload or {})
    body.setdefault("type", event)
    return f"event: {event}\ndata: {json.dumps(body, ensure_ascii=False, default=str)}\n\n"


def _to_stream_mode_chunk(item: Any) -> tuple[str, Any]:
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], str)
    ):
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

    if _is_sync_sqlite_checkpointer(checkpointer_type, checkpointer_module):
        logger.info(
            "AGENT_STREAM_START mode=sync_sqlite graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in _stream_agent_sync_fallback(
            agent,
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=sync_sqlite checkpointer=%s",
            checkpointer_type,
        )
        return

    try:
        logger.info(
            "AGENT_STREAM_START mode=async graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in agent.astream(
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=async checkpointer=%s",
            checkpointer_type,
        )
    except (TypeError, NotImplementedError) as exc:
        error_text = str(exc)
        if (
            "does not support async methods" not in error_text
            and "AsyncSqliteSaver" not in error_text
        ):
            raise
        logger.warning(
            "Agent async stream is unavailable for current checkpointer; "
            "falling back to sync stream in thread pool.",
        )
        logger.info(
            "AGENT_STREAM_START mode=sync_fallback graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in _stream_agent_sync_fallback(
            agent,
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=sync_fallback checkpointer=%s",
            checkpointer_type,
        )
    except Exception:
        logger.exception(
            "AGENT_STREAM_FAILED checkpointer=%s module=%s",
            checkpointer_type,
            checkpointer_module,
        )
        raise


@traceable(name="api_agent_invoke")
async def run_agent_async(agent, graph_input: Any, config: dict[str, Any] | None = None):
    """Run the agent asynchronously."""
    checkpointer = getattr(agent, "checkpointer", None)
    checkpointer_type = type(checkpointer).__name__
    checkpointer_module = getattr(type(checkpointer), "__module__", "")

    if (
        checkpointer_type == "SqliteSaver"
        and "langgraph.checkpoint.sqlite" in checkpointer_module
        and ".aio" not in checkpointer_module
    ):
        logger.info(
            "AGENT_INVOKE_START mode=sync_sqlite graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await asyncio.to_thread(agent.invoke, graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=sync_sqlite elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result

    try:
        logger.info(
            "AGENT_INVOKE_START mode=async graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await agent.ainvoke(graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=async elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result
    except (TypeError, NotImplementedError) as exc:
        error_text = str(exc)
        if (
            "does not support async methods" not in error_text
            and "AsyncSqliteSaver" not in error_text
        ):
            raise
        logger.warning(
            "Agent async invocation is unavailable for current checkpointer; "
            "falling back to sync invoke in thread pool.",
        )
        logger.info(
            "AGENT_INVOKE_START mode=sync_fallback graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await asyncio.to_thread(agent.invoke, graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=sync_fallback elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result
    except Exception as exc:
        logger.exception(
            "AGENT_INVOKE_FAILED mode=unknown checkpointer=%s module=%s error=%s",
            checkpointer_type,
            checkpointer_module,
            exc,
        )
        logger.exception("Error running agent: %s", exc)
        raise

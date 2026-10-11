"""Optional local timings, correlated across requests and worker threads.

Set PERFORMANCE_TRACE_FILE to opt in. Durations of nested/parallel spans are
observations, not additive components of the HTTP wall-clock duration.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class TraceContext:
    request_id: str
    span_id: str


_CONTEXT: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar("performance_context", default=None)


def _run_id() -> str | None:
    from .execution_control import current_control

    control = current_control()
    return control.run_id if control else None


def write_record(record: dict[str, Any]) -> None:
    destination = os.environ.get("PERFORMANCE_TRACE_FILE")
    if not destination:
        return
    try:
        with _WRITE_LOCK:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.warning("Could not write performance trace", exc_info=True)


@contextmanager
def span(stage: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    if not os.environ.get("PERFORMANCE_TRACE_FILE"):
        yield attributes
        return
    parent = _CONTEXT.get()
    identifier = uuid.uuid4().hex
    token = _CONTEXT.set(TraceContext(parent.request_id if parent else "", identifier))
    started = time.perf_counter_ns()
    outcome = "success"
    try:
        yield attributes
    except BaseException as exc:
        outcome = "interrupted" if type(exc).__name__ in {"GraphInterrupt", "NodeInterrupt"} else "error"
        attributes["error_type"] = type(exc).__name__
        raise
    finally:
        finished = time.perf_counter_ns()
        write_record(
            {
                "kind": "span",
                "stage": stage,
                "span_id": identifier,
                "parent_span_id": parent.span_id if parent else None,
                "request_id": parent.request_id if parent else "",
                "run_id": _run_id(),
                "start_ns": started,
                "end_ns": finished,
                "elapsed_ms": (finished - started) / 1_000_000,
                "outcome": outcome,
                **attributes,
            }
        )
        _CONTEXT.reset(token)


def timed(stage: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            with span(stage):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def record_elapsed_event(event: str, fields: dict[str, Any]) -> None:
    """Preserve existing duration events without copying prompts or secrets."""
    context = _CONTEXT.get()
    if context is None or not os.environ.get("PERFORMANCE_TRACE_FILE"):
        return
    duration = fields.get("elapsed_ms", fields.get("latency_ms"))
    failed = event.endswith((".fail", ".failed", ".degraded")) or bool(fields.get("fallback_used"))
    has_duration = isinstance(duration, (int, float))
    if not has_duration and not failed:
        return
    allowed = ("scene", "model", "tool", "channel", "error_type", "fallback_used", "retries", "mode", "docs", "top_n")
    write_record(
        {
            "kind": "duration_event" if has_duration else "diagnostic",
            "stage": event,
            "span_id": uuid.uuid4().hex,
            "parent_span_id": context.span_id,
            "request_id": context.request_id,
            "run_id": _run_id(),
            "end_ns": time.perf_counter_ns(),
            **({"elapsed_ms": duration} if has_duration else {}),
            "outcome": "error" if failed else "success",
            **{key: fields[key] for key in allowed if key in fields},
        }
    )


class TimingMiddleware:
    """ASGI middleware: encompasses the full response stream and cancellation."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        candidate = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = candidate if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", candidate) else uuid.uuid4().hex
        root_id = uuid.uuid4().hex
        token = _CONTEXT.set(TraceContext(request_id, root_id))
        started = time.perf_counter_ns()
        status = 500
        complete = False
        disconnected = False
        error_type = None

        async def traced_receive() -> dict[str, Any]:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def traced_send(message: dict[str, Any]) -> None:
            nonlocal status, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [
                    (name, value) for name, value in message.get("headers", []) if name.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        try:
            await self.app(scope, traced_receive, traced_send)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            finished = time.perf_counter_ns()
            write_record(
                {
                    "kind": "span",
                    "stage": "http",
                    "request_id": request_id,
                    "span_id": root_id,
                    "parent_span_id": None,
                    "run_id": None,
                    "method": scope["method"],
                    "path": scope["path"],
                    "start_ns": started,
                    "end_ns": finished,
                    "elapsed_ms": (finished - started) / 1_000_000,
                    "http_status": status,
                    "complete": complete,
                    "error_type": error_type,
                    "outcome": "success"
                    if complete and status < 400 and not error_type
                    else "cancelled"
                    if disconnected
                    else "error",
                }
            )
            _CONTEXT.reset(token)

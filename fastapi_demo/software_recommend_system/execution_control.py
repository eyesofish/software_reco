"""Cooperative limits for a single recommendation graph execution."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import queue
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langgraph.errors import GraphRecursionError

logger = logging.getLogger(__name__)
_MAX_ACTIVE_RUNS = 4
_STREAM_BUFFER_SIZE = 64
_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_ACTIVE_RUNS,
    thread_name_prefix="agent-run",
)
_CAPACITY = threading.BoundedSemaphore(_MAX_ACTIVE_RUNS)
_LOCK = threading.Lock()
_ACTIVE_SESSIONS: dict[str, RunControl] = {}
_CURRENT_CONTROL: contextvars.ContextVar[RunControl | None] = contextvars.ContextVar(
    "agent_execution_control", default=None
)
_T = TypeVar("_T")


class ExecutionStopped(BaseException):
    """Raised inside graph work to bypass broad fallback handlers after a stop."""


class RunStopped(Exception):
    def __init__(self, reason: str, control: RunControl) -> None:
        self.stop_reason = reason
        self.run_id = control.run_id
        self.elapsed_ms = control.elapsed_ms
        super().__init__(f"Agent run {reason} (run_id={self.run_id})")


class RunCapacityExceeded(Exception):
    pass


class SessionRunConflict(Exception):
    pass


@dataclass
class RunControl:
    session_id: str
    timeout_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stop_event: threading.Event = field(default_factory=threading.Event)
    stop_reason: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self.started_at) * 1000))

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (time.monotonic() - self.started_at))

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self.stop_reason is None:
                self.stop_reason = reason
                self.stop_event.set()

    def check(self) -> None:
        if self.stop_event.is_set():
            raise ExecutionStopped(self.stop_reason or "cancelled")
        if self.remaining_seconds <= 0:
            self.cancel("timeout")
            raise ExecutionStopped("timeout")

    def request_timeout(self, default_seconds: float) -> float:
        self.check()
        return max(0.05, min(default_seconds, self.remaining_seconds))


def current_control() -> RunControl | None:
    return _CURRENT_CONTROL.get()


def check_current_run() -> None:
    control = current_control()
    if control is not None:
        control.check()


def current_request_timeout(default_seconds: float) -> float:
    control = current_control()
    return control.request_timeout(default_seconds) if control else default_seconds


def check_for_session(session_id: str) -> None:
    with _LOCK:
        control = _ACTIVE_SESSIONS.get(str(session_id or ""))
    if control is not None:
        control.check()


def _begin_run(session_id: str, timeout_seconds: float) -> RunControl:
    normalized_session = str(session_id or uuid.uuid4().hex)
    control = RunControl(normalized_session, max(0.001, float(timeout_seconds)))
    with _LOCK:
        if normalized_session in _ACTIVE_SESSIONS:
            raise SessionRunConflict(f"A recommendation run is already active for session {normalized_session}")
        _ACTIVE_SESSIONS[normalized_session] = control
    if not _CAPACITY.acquire(blocking=False):
        _finish_run(control)
        raise RunCapacityExceeded("Agent execution capacity is exhausted; retry shortly")
    return control


def _finish_run(control: RunControl) -> None:
    with _LOCK:
        if _ACTIVE_SESSIONS.get(control.session_id) is control:
            del _ACTIVE_SESSIONS[control.session_id]


def _run_in_worker(control: RunControl, operation: Callable[[], _T]) -> _T:
    token = _CURRENT_CONTROL.set(control)
    try:
        control.check()
        result = operation()
        control.check()
        return result
    finally:
        _CURRENT_CONTROL.reset(token)
        _finish_run(control)
        _CAPACITY.release()


async def run_controlled(
    operation: Callable[[RunControl], _T], *, session_id: str, timeout_seconds: float
) -> tuple[_T, RunControl]:
    control = _begin_run(session_id, timeout_seconds)
    try:
        worker_future = _POOL.submit(_run_in_worker, control, lambda: operation(control))
    except BaseException:
        _finish_run(control)
        _CAPACITY.release()
        raise
    wrapped_future = asyncio.wrap_future(worker_future)
    try:
        result = await asyncio.wait_for(wrapped_future, timeout=control.remaining_seconds)
        return result, control
    except ExecutionStopped as exc:
        reason = control.stop_reason or str(exc)
        control.cancel(reason)
        raise RunStopped(reason, control) from exc
    except GraphRecursionError as exc:
        control.cancel("recursion_limit")
        raise RunStopped("recursion_limit", control) from exc
    except TimeoutError as exc:
        control.cancel("timeout")
        worker_future.cancel()
        raise RunStopped("timeout", control) from exc
    except asyncio.CancelledError:
        control.cancel("cancelled")
        worker_future.cancel()
        raise


async def stream_controlled(
    stream_factory: Callable[[RunControl], Iterator[Any]],
    *,
    session_id: str,
    timeout_seconds: float,
) -> AsyncIterator[tuple[Any, RunControl]]:
    control = _begin_run(session_id, timeout_seconds)
    messages: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=_STREAM_BUFFER_SIZE)

    def publish(message: tuple[str, Any]) -> bool:
        while not control.stop_event.is_set():
            try:
                messages.put(message, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        token = _CURRENT_CONTROL.set(control)
        try:
            control.check()
            iterator = stream_factory(control)
            for item in iterator:
                control.check()
                if not publish(("item", item)):
                    break
            else:
                publish(("done", None))
        except BaseException as exc:
            if isinstance(exc, GraphRecursionError):
                control.cancel("recursion_limit")
            publish(("error", exc))
        finally:
            _CURRENT_CONTROL.reset(token)
            _finish_run(control)
            _CAPACITY.release()

    completed = False
    try:
        try:
            _POOL.submit(produce)
        except BaseException:
            _finish_run(control)
            _CAPACITY.release()
            raise
        while True:
            control.check()
            try:
                message_type, payload = messages.get_nowait()
            except queue.Empty:
                await asyncio.sleep(min(0.01, control.remaining_seconds))
                continue
            if message_type == "done":
                completed = True
                yield (None, control)
                return
            if message_type == "error":
                if isinstance(payload, ExecutionStopped):
                    raise RunStopped(str(payload), control)
                raise payload
            yield (payload, control)
    except TimeoutError as exc:
        control.cancel("timeout")
        raise RunStopped("timeout", control) from exc
    except asyncio.CancelledError:
        control.cancel("cancelled")
        raise
    except ExecutionStopped as exc:
        reason = str(exc) if str(exc) in {"timeout", "cancelled"} else control.stop_reason
        control.cancel(reason or "cancelled")
        raise RunStopped(control.stop_reason or "cancelled", control) from exc
    finally:
        if not completed and not control.stop_event.is_set():
            control.cancel("cancelled")

"""Bounded observation-only routing worker. It never imports the Agent or stores."""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .config import settings
from .intent_routing import RoutingSnapshot, decide_choices, resolve_routing
from .jev_provider import JevProvider

logger = logging.getLogger(__name__)
_FILE_LOCK = threading.Lock()


def snapshot_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record(record: dict[str, Any]) -> None:
    serialized = json.dumps(record, ensure_ascii=False, default=str)
    logger.info(serialized)
    filename = str(getattr(settings, "ROUTING_SHADOW_TRACE_FILE", "") or "").strip()
    if filename:
        with _FILE_LOCK:
            path = Path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")


class RoutingShadow:
    """Separate queue/provider/circuit; input is serialized before returning to the caller."""

    def __init__(
        self,
        *,
        capacity: int = 64,
        provider: Any = None,
        sink: Callable[[dict[str, Any]], None] | None = None,
        resolver: Callable[..., Any] | None = None,
        reviewer: Callable[..., Any] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("shadow capacity must be positive")
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=capacity)
        self._provider = provider or JevProvider()
        self._sink = sink or _record
        self._resolver = resolver or resolve_routing
        self._reviewer = reviewer
        self._stats: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._worker = threading.Thread(target=self._run, name="routing-shadow", daemon=True)
        self._worker.start()

    def _emit(self, record: dict[str, Any]) -> None:
        record = {"event": "routing.shadow", "shadow": True, "occurred_at": datetime.now(UTC).isoformat(), **record}
        try:
            self._sink(record)
        except Exception:
            # Diagnostics may be dropped; a sink failure must never affect a live request.
            with self._lock:
                self._stats["sink_failures"] += 1
            logger.warning("routing shadow diagnostic sink failed")

    def _submit(self, kind: str, payload: dict[str, Any]) -> bool:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        snapshot_id = payload.get("snapshot_id", "")
        with self._lock:
            self._stats["submitted"] += 1
            if self._closed.is_set():
                self._stats["dropped"] += 1
                accepted = False
            else:
                try:
                    self._queue.put_nowait((kind, raw))
                    accepted = True
                except queue.Full:
                    self._stats["dropped"] += 1
                    accepted = False
        if not accepted:
            self._emit({"snapshot_id": snapshot_id, "stage": kind, "outcome": "dropped"})
        return accepted

    def submit(self, snapshot: RoutingSnapshot | dict[str, Any]) -> bool:
        frozen = RoutingSnapshot.model_validate(snapshot).model_dump(mode="json")
        return self._submit("routing", frozen)

    def submit_choices(
        self,
        *,
        snapshot_id: str,
        questions: dict[str, Any],
        state: dict[str, Any],
        reviews_used: int = 0,
    ) -> bool:
        return self._submit(
            "runtime",
            {
                "snapshot_id": snapshot_id,
                "questions": questions,
                "state": state,
                "reviews_used": reviews_used,
            },
        )

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                kind, raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            payload = json.loads(raw)
            record = {
                "snapshot_id": payload.get("snapshot_id", ""),
                "stage": kind,
                "snapshot_hash": snapshot_hash(payload),
            }
            try:
                if kind == "routing":
                    result = self._resolver(payload, client=self._provider, reviewer=self._reviewer)
                else:
                    result = decide_choices(
                        payload["questions"],
                        payload["state"],
                        reviews_used=payload["reviews_used"],
                        timeout_seconds=float(getattr(settings, "JEV_TIMEOUT_SECONDS", 8)),
                        request_id=payload["snapshot_id"],
                        client=self._provider,
                        reviewer=self._reviewer,
                    )
                output = cast(
                    dict[str, Any], result.model_dump(mode="json") if hasattr(result, "model_dump") else result
                )
                decisions = output.get("decisions", {})
                failed = output.get("source") == "fallback" or any(
                    value.get("source") == "fallback" for value in decisions.values()
                )
                record.update(outcome="failed" if failed else "completed", decision=output)
                with self._lock:
                    self._stats["failed" if failed else "completed"] += 1
            except Exception as exc:
                record.update(outcome="failed", error_type=type(exc).__name__)
                with self._lock:
                    self._stats["failed"] += 1
            finally:
                self._emit(record)
                self._queue.task_done()

    def statistics(self) -> dict[str, int]:
        with self._lock:
            return {key: self._stats[key] for key in ("submitted", "completed", "failed", "dropped", "sink_failures")}

    def close(self, *, wait_seconds: float = 10) -> None:
        self._closed.set()
        self._worker.join(timeout=wait_seconds)


_DEFAULT: RoutingShadow | None = None
_DEFAULT_LOCK = threading.Lock()


def default_shadow() -> RoutingShadow:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = RoutingShadow(capacity=int(getattr(settings, "ROUTING_SHADOW_QUEUE_CAPACITY", 64)))
        return _DEFAULT


def submit_shadow(snapshot: RoutingSnapshot | dict[str, Any]) -> bool:
    return default_shadow().submit(snapshot)

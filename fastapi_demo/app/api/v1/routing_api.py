"""Read-only preflight plus exact, checkpoint-backed task clarification."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from starlette.background import BackgroundTask

from app.core.config import settings
from software_recommend_system.intent_routing import ChoiceDecision, RoutingResolution, RoutingSnapshot, resolve_routing

from .auth import require_api_key
from .models import CancelWaitingRequest, ClarificationRequest, RecommendationRequest, RecommendationResponse
from .session_store import _append_session_message_once, peek_session_state
from .stream_utils import _sse

router = APIRouter(dependencies=[Depends(require_api_key)])
_RESUME_LOCKS: dict[str, asyncio.Lock] = {}


def _graph() -> Any:
    from . import routes

    return routes.AGENT


def checkpoint_values(session_id: str) -> dict[str, Any]:
    value = _graph().get_state({"configurable": {"thread_id": session_id}})
    return dict(value.values) if isinstance(getattr(value, "values", None), dict) else {}


def _clarification(snapshot: RoutingSnapshot, reason: str = "请选择本条消息的会话归属。") -> RoutingResolution:
    options = [{"kind": "new", "title": "新建会话"}]
    if snapshot.current_conversation_id:
        options.insert(
            0, {"kind": "existing", "conversation_id": snapshot.current_conversation_id, "title": "继续当前会话"}
        )
    return RoutingResolution(
        snapshot_id=snapshot.snapshot_id,
        status="routing_clarification",
        source="structured",
        clarification={"kind": "routing", "clarification_id": uuid4().hex, "prompt": reason, "options": options},
    )


def _in_cohort(identifier: str) -> bool:
    bucket = int.from_bytes(hashlib.sha256(identifier.encode("utf-8")).digest()[:8], "big") % 100
    return bucket < settings.ROUTING_TRAFFIC_PERCENT


def semantic_auto_allowed(identifier: str) -> bool:
    from evaluation.routing_evaluate import load_acceptance_gate

    return bool(
        settings.ROUTING_DEPLOYMENT_MODE == "live"
        and settings.ROUTING_AUTO_SESSION_APPROVED
        and _in_cohort(identifier)
        and settings.ROUTING_GATE_REPORT
        and load_acceptance_gate(settings.ROUTING_GATE_REPORT)
    )


async def bounded_resolve(snapshot: RoutingSnapshot) -> RoutingResolution:
    started = time.monotonic()
    timeout = min(snapshot.timeout_seconds, settings.JEV_TIMEOUT_SECONDS)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(resolve_routing, snapshot.model_copy(deep=True)),
            timeout=timeout,
        )
    except TimeoutError:
        if snapshot.binding == "unresolved":
            result = _clarification(snapshot, "归属判断暂时不可用，请明确选择会话。")
            result.source = "fallback"
        else:
            result = RoutingResolution(
                snapshot_id=snapshot.snapshot_id,
                target=snapshot.binding,
                target_session_id=snapshot.target_session_id if snapshot.binding == "existing" else None,
                task_relation="continue" if snapshot.binding == "existing" else "new",
                mode="rag",
                source="fallback",
            )
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        result.decisions["provider"] = ChoiceDecision(source="fallback", reason="preflight_timeout")
        return result


@router.post("/routing/resolve", response_model=RoutingResolution)
async def routing_resolve(snapshot: RoutingSnapshot) -> RoutingResolution:
    if snapshot.binding == "unresolved" and not semantic_auto_allowed(
        snapshot.cohort_key or snapshot.current_conversation_id or snapshot.snapshot_id
    ):
        return _clarification(snapshot, "自动归属尚未通过上线门槛，请明确选择当前会话或新建会话。")
    return await bounded_resolve(snapshot)


@router.post("/routing/shadow", status_code=202)
async def routing_shadow(snapshot: RoutingSnapshot) -> dict[str, Any]:
    from software_recommend_system.routing_shadow import submit_shadow

    accepted = submit_shadow(snapshot)
    return {"accepted": accepted, "snapshot_id": snapshot.snapshot_id}


async def preflight(request: RecommendationRequest) -> RecommendationResponse | None:
    """Resolve direct service callers before any session write; Java supplies trusted results."""
    started = time.monotonic()
    from .handlers.normalizers import _is_confirmation_short_query

    short_confirmation = not request.images and _is_confirmation_short_query(request.query)
    if request.session_id:
        if _RESUME_LOCKS.get(request.session_id) and _RESUME_LOCKS[request.session_id].locked():
            raise HTTPException(409, "This session is already resuming a task")
        snapshot_state = await asyncio.to_thread(
            _graph().get_state,
            {"configurable": {"thread_id": request.session_id}},
        )
        values = snapshot_state.values if isinstance(getattr(snapshot_state, "values", None), dict) else {}
        if values.get("pending_input") and not values.get("execution_cancelled"):
            raise HTTPException(409, "This session is waiting for task clarification")
        if values.get("execution_cancelled") and short_confirmation:
            raise HTTPException(409, "Task has been cancelled")
        if _interrupt_type(snapshot_state) == "human_confirmation" and not short_confirmation:
            raise HTTPException(409, "This session is waiting for confirmation; confirm or cancel it first")
    # A checkpoint resume is a structured operation, so it never needs intent classification.
    if short_confirmation:
        return None
    if request.routing_decision is not None:
        result = RoutingResolution.model_validate(request.routing_decision)
        if result.status != "ready":
            return RecommendationResponse(
                status="routing_clarification", final_answer="", clarification=result.clarification
            )
        if result.target == "existing" and result.target_session_id != request.session_id:
            raise HTTPException(409, "Resolved routing does not match execution session")
        return None
    if request.routing_prechecked:
        if not request.session_id:
            raise HTTPException(422, "Prechecked execution requires a resolved session")
        return None
    snapshot = RoutingSnapshot(
        query=request.query,
        binding="existing" if request.session_id else ("unresolved" if request.routing_mode == "auto" else "new"),
        target_session_id=request.session_id,
        current_conversation_id=request.current_conversation_id,
        context=await asyncio.to_thread(
            peek_session_state, request.session_id or request.current_conversation_id or ""
        ),
        has_images=bool(request.images),
        timeout_seconds=min(
            settings.JEV_TIMEOUT_SECONDS,
            request.timeout,
            request.execution_budget_seconds or request.timeout,
        ),
    )
    if settings.ROUTING_DEPLOYMENT_MODE == "shadow":
        from software_recommend_system.routing_shadow import submit_shadow

        submit_shadow(snapshot)
    cohort = request.session_id or request.current_conversation_id or snapshot.snapshot_id
    live = settings.ROUTING_DEPLOYMENT_MODE == "live" and _in_cohort(cohort)
    if snapshot.binding == "unresolved" and not semantic_auto_allowed(cohort):
        result = _clarification(snapshot)
    elif live:
        result = await bounded_resolve(snapshot)
    else:
        return None
    if result.status == "routing_clarification":
        return RecommendationResponse(status=result.status, final_answer="", clarification=result.clarification)
    if result.target == "existing":
        request.session_id = cast(str, result.target_session_id)
        if _RESUME_LOCKS.get(request.session_id) and _RESUME_LOCKS[request.session_id].locked():
            raise HTTPException(409, "This session is already resuming a task")
        values = await asyncio.to_thread(checkpoint_values, request.session_id)
        if values.get("pending_input") and not values.get("execution_cancelled"):
            raise HTTPException(409, "This session is waiting for task clarification")
        if (
            values.get("awaiting_human_confirmation")
            and not values.get("human_confirmation_done")
            and not values.get("execution_cancelled")
        ):
            raise HTTPException(409, "This session is waiting for confirmation; confirm or cancel it first")
    request.routing_decision = result.model_dump()
    request.execution_budget_seconds = min(request.timeout, request.execution_budget_seconds or request.timeout) - (
        time.monotonic() - started
    )
    if request.execution_budget_seconds <= 0:
        raise HTTPException(408, "Routing exceeded the request deadline")
    return None


def graph_routing_fields(request: RecommendationRequest) -> dict[str, Any]:
    resolution = request.routing_decision or {}
    remaining = min(request.timeout, request.execution_budget_seconds or request.timeout)
    return {
        "routing_decision": request.routing_decision,
        "dynamic_routing_enabled": bool(settings.DYNAMIC_ROUTING_ENABLE and resolution),
        "routing_reviews_used": resolution.get("reviews_used", 0),
        "active_elapsed_ms": max(0, int((request.timeout - remaining) * 1000)),
    }


def _interrupt_type(snapshot: Any) -> str | None:
    for task in getattr(snapshot, "tasks", ()):
        for item in getattr(task, "interrupts", ()):
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                return value.get("type")
    # Saving the active-time checkpoint can clear task-local interrupt objects;
    # the waiting fields and next node remain authoritative in that checkpoint.
    values = getattr(snapshot, "values", None)
    if getattr(snapshot, "next", ()) and isinstance(values, dict):
        if values.get("pending_input"):
            return "awaiting_input"
        if values.get("awaiting_human_confirmation") and not values.get("human_confirmation_done"):
            return "human_confirmation"
    return None


@dataclass
class ResumeLease:
    lock: asyncio.Lock
    released: bool = False

    def locked(self) -> bool:
        return not self.released and self.lock.locked()

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.lock.release()


def release_resume(lock: asyncio.Lock | ResumeLease) -> None:
    if lock.locked():
        lock.release()


async def reserve_resume(
    session_id: str,
    *,
    kind: str,
    clarification_id: str | None = None,
) -> tuple[dict[str, Any], ResumeLease]:
    """Reserve before response headers so stale/concurrent requests receive HTTP 409."""
    lock = _RESUME_LOCKS.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "Task is already resuming")
    await lock.acquire()
    try:
        snapshot = await asyncio.to_thread(_graph().get_state, {"configurable": {"thread_id": session_id}})
        values = snapshot.values if isinstance(getattr(snapshot, "values", None), dict) else {}
        pending = values.get("pending_input") or {}
        if values.get("execution_cancelled"):
            raise HTTPException(409, "Task has been cancelled")
        interrupt_type = _interrupt_type(snapshot)
        if kind == "input":
            if not pending or pending.get("clarification_id") != clarification_id:
                raise HTTPException(409, "Stale clarification")
            if not getattr(snapshot, "next", ()):
                raise HTTPException(409, "Task is not waiting for input")
        elif kind == "confirmation":
            if pending or interrupt_type == "awaiting_input":
                raise HTTPException(409, "Use the clarification endpoint for this task")
            if interrupt_type != "human_confirmation":
                raise HTTPException(409, "No pending human confirmation")
        elif kind == "cancel":
            if clarification_id and pending.get("clarification_id") != clarification_id:
                raise HTTPException(409, "Stale clarification")
            if not pending and interrupt_type != "human_confirmation":
                raise HTTPException(409, "Task is not waiting")
        return dict(values), ResumeLease(lock)
    except BaseException:
        release_resume(lock)
        raise


async def _resume(request: ClarificationRequest) -> RecommendationResponse:
    from .handlers.recommend_orchestrator import execute_recommend_turn

    if not request.answer.strip():
        raise HTTPException(422, "A clarification answer is required")
    values, lock = await reserve_resume(request.session_id, kind="input", clarification_id=request.clarification_id)
    try:
        await asyncio.to_thread(_append_session_message_once, request.session_id, "user", request.answer.strip())
        return cast(
            RecommendationResponse,
            await execute_recommend_turn(
                session_id=request.session_id,
                request_query=values.get("user_query", ""),
                graph_input=Command(resume=request.model_dump()),
                config={"configurable": {"thread_id": request.session_id}},
                interrupt_source="clarification",
                agent=_graph(),
            ),
        )
    finally:
        release_resume(lock)


@router.post("/recommend/clarify", response_model=RecommendationResponse)
async def clarify(request: ClarificationRequest) -> RecommendationResponse:
    return await _resume(request)


@router.post("/recommend/clarify/stream")
async def clarify_stream(request: ClarificationRequest) -> StreamingResponse:
    from .handlers.recommend_orchestrator import stream_recommend_turn

    if not request.answer.strip():
        raise HTTPException(422, "A clarification answer is required")
    values, lock = await reserve_resume(request.session_id, kind="input", clarification_id=request.clarification_id)

    async def events() -> AsyncIterator[str]:
        try:
            yield _sse(
                "resume_accepted",
                {
                    "session_id": request.session_id,
                    "kind": "clarification",
                    "clarification_id": request.clarification_id,
                },
            )
            await asyncio.to_thread(_append_session_message_once, request.session_id, "user", request.answer.strip())
            async for frame in stream_recommend_turn(
                agent=_graph(),
                session_id=request.session_id,
                request_query=values.get("user_query", ""),
                graph_input=Command(resume=request.model_dump()),
                config={"configurable": {"thread_id": request.session_id}},
            ):
                yield frame
        finally:
            release_resume(lock)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        background=BackgroundTask(release_resume, lock),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/recommend/cancel")
async def cancel_waiting(request: CancelWaitingRequest) -> dict[str, str]:
    _, lock = await reserve_resume(request.session_id, kind="cancel", clarification_id=request.clarification_id)
    try:
        await asyncio.to_thread(
            _graph().update_state,
            {"configurable": {"thread_id": request.session_id}},
            {"execution_cancelled": True, "pending_input": None},
        )
        return {"status": "CANCELLED", "session_id": request.session_id}
    finally:
        release_resume(lock)

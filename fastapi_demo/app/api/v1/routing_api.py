"""Read-only routing preflight and session assignment."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from software_recommend_system.intent_routing import ChoiceDecision, RoutingResolution, RoutingSnapshot, resolve_routing

from .auth import require_api_key
from .models import RecommendationRequest, RecommendationResponse
from .session_store import peek_session_state

router = APIRouter(dependencies=[Depends(require_api_key)])


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
    request.routing_decision = result.model_dump()
    request.execution_budget_seconds = min(request.timeout, request.execution_budget_seconds or request.timeout) - (
        time.monotonic() - started
    )
    if request.execution_budget_seconds <= 0:
        raise HTTPException(408, "Routing exceeded the request deadline")
    return None

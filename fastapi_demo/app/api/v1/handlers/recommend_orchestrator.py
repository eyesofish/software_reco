"""Recommend-turn orchestration: agent invocation, HITL handling, response shaping.

This sits between the route handlers (which deal with HTTP and SSE concerns)
and the agent graph (which produces the underlying result). Pure async; no
FastAPI-specific imports beyond response models.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from software_recommend_system.observability import traceable

from ..models import RecommendationResponse
from ..session_store import (
    _append_session_message_once,
    _get_or_create_session_state,
    _merge_session_facts,
    _safe_write_turn_memories,
)
from ..stream_utils import run_agent_async
from ._agent import AGENT
from .normalizers import (
    _build_human_confirmation_ack,
    _extract_eval_payload,
    _extract_interrupt_payload,
    _extract_name_fact,
    _extract_skill_planner_payload,
    _get_value,
    _normalize_candidates,
    _normalize_hitl_payload,
    _normalize_pending_sub_questions,
)

logger = logging.getLogger(__name__)


def _build_success_response(
    *,
    session_id: str,
    final_answer: str,
    mode: str,
    coverage: float,
    result: Any,
    skill_planner_payload: dict[str, Any],
    eval_payload: dict[str, Any],
    candidates: list[dict[str, Any]] | None = None,
) -> RecommendationResponse:
    """Build a success RecommendationResponse from agent result + extracted payloads."""
    if candidates is None:
        candidates = _normalize_candidates(_get_value(result, "candidates", []))
    return RecommendationResponse(
        status="success",
        final_answer=final_answer,
        candidates=candidates,
        mode=mode,
        iteration_count=_get_value(result, "iteration_count", 0),
        coverage=coverage,
        session_id=session_id,
        awaiting_human_confirmation=False,
        retrieval_records=eval_payload["retrieval_records"],
        retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
        retrieved_images=eval_payload["retrieved_images"],
        selected_skill=skill_planner_payload["selected_skill"],
        plan_steps=skill_planner_payload["plan_steps"],
        hitl=eval_payload["hitl"],
    )


@traceable(name="api_recommend_turn")
async def execute_recommend_turn(
    *,
    session_id: str,
    request_query: str,
    graph_input: Any,
    config: dict[str, Any],
    fallback_answer: str | None = None,
    fallback_mode: str = "direct",
    interrupt_source: str = "recommend",
) -> RecommendationResponse:
    try:
        result = await run_agent_async(AGENT, graph_input, config=config)
        hitl_policy = str(_get_value(graph_input, "hitl_policy", "human") or "human").strip()
        if hitl_policy not in {"human", "auto_confirm", "oracle_edit"}:
            hitl_policy = "human"
        eval_payload = _extract_eval_payload(
            result,
            hitl_fallback={
                "policy": hitl_policy,
                "decision": "not_applicable",
                "edited_subqueries": [],
            },
        )
        skill_planner_payload = _extract_skill_planner_payload(result)
        interrupt_payload = _extract_interrupt_payload(result)
        if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
            pending_sub_questions = _normalize_pending_sub_questions(
                interrupt_payload.get("sub_questions", [])
            )
            if not pending_sub_questions:
                logger.error(
                    "HITL_ACK_ABORT session_id=%s reason=empty_sub_questions payload=%s source=%s",
                    session_id,
                    interrupt_payload,
                    interrupt_source,
                )
                raise RuntimeError(
                    "Interrupted for human confirmation but no sub-questions were produced."
                )
            ack_message = _build_human_confirmation_ack(pending_sub_questions)
            logger.warning(
                "HITL_ACK_RETURN session_id=%s pending_count=%d source=%s",
                session_id,
                len(pending_sub_questions),
                interrupt_source,
            )
            _append_session_message_once(session_id, "assistant", ack_message)
            return RecommendationResponse(
                status="awaiting_human_confirmation",
                final_answer=ack_message,
                candidates=[],
                mode="rag",
                iteration_count=0,
                coverage=0.0,
                session_id=session_id,
                awaiting_human_confirmation=True,
                pending_sub_questions=pending_sub_questions,
                retrieval_records=eval_payload["retrieval_records"],
                retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
                retrieved_images=eval_payload["retrieved_images"],
                selected_skill=skill_planner_payload["selected_skill"],
                plan_steps=skill_planner_payload["plan_steps"],
                hitl=eval_payload["hitl"]
                or _normalize_hitl_payload(
                    {
                        "policy": hitl_policy,
                        "decision": "awaiting_confirmation",
                        "edited_subqueries": [],
                    }
                ),
            )

        final_answer = _get_value(result, "final_answer", "")
        if final_answer:
            _append_session_message_once(session_id, "assistant", final_answer)
            response = _build_success_response(
                session_id=session_id,
                final_answer=final_answer,
                mode=_get_value(result, "mode", ""),
                coverage=_get_value(result, "coverage", 0.0),
                result=result,
                skill_planner_payload=skill_planner_payload,
                eval_payload=eval_payload,
            )
            _safe_write_turn_memories(
                session_id=session_id,
                request_query=request_query,
                final_answer=response.final_answer,
                selected_skill=response.selected_skill,
                retrieved_doc_ids=list(response.retrieved_doc_ids or []),
            )
            return response

        if fallback_answer:
            _append_session_message_once(session_id, "assistant", fallback_answer)
            fallback_eval_payload = _extract_eval_payload(
                result,
                hitl_fallback={
                    "policy": hitl_policy,
                    "decision": "confirm",
                    "edited_subqueries": [],
                },
            )
            response = _build_success_response(
                session_id=session_id,
                final_answer=fallback_answer,
                mode=fallback_mode,
                coverage=1.0,
                result=result,
                skill_planner_payload=skill_planner_payload,
                eval_payload=fallback_eval_payload,
                candidates=[],
            )
            _safe_write_turn_memories(
                session_id=session_id,
                request_query=request_query,
                final_answer=response.final_answer,
                selected_skill=response.selected_skill,
                retrieved_doc_ids=list(response.retrieved_doc_ids or []),
            )
            return response

        response = _build_success_response(
            session_id=session_id,
            final_answer=final_answer,
            mode=_get_value(result, "mode", ""),
            coverage=_get_value(result, "coverage", 0.0),
            result=result,
            skill_planner_payload=skill_planner_payload,
            eval_payload=eval_payload,
        )
        _safe_write_turn_memories(
            session_id=session_id,
            request_query=request_query,
            final_answer=response.final_answer,
            selected_skill=response.selected_skill,
            retrieved_doc_ids=list(response.retrieved_doc_ids or []),
        )
        return response
    except Exception:
        logger.exception(
            "RECOMMEND_TURN_FAILED session_id=%s source=%s query=%r",
            session_id,
            interrupt_source,
            request_query,
        )
        _append_session_message_once(
            session_id,
            "system",
            "Request failed while processing this turn.",
        )
        raise


def _log_recommend_task_outcome(task: asyncio.Task, session_id: str, source: str) -> None:
    if task.cancelled():
        logger.warning("RECOMMEND_TASK_CANCELLED session_id=%s source=%s", session_id, source)
        return
    exc = task.exception()
    if exc:
        logger.error(
            "RECOMMEND_TASK_FAILED session_id=%s source=%s error=%s",
            session_id,
            source,
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def create_recommend_task(coro: Any, session_id: str, source: str) -> asyncio.Task:
    task = asyncio.create_task(coro)
    task.add_done_callback(
        lambda done_task: _log_recommend_task_outcome(done_task, session_id, source)
    )
    return task


async def await_recommend_task(
    task: asyncio.Task,
    session_id: str,
    source: str,
) -> RecommendationResponse:
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        logger.warning("CLIENT_DISCONNECTED_CONTINUE session_id=%s source=%s", session_id, source)
        raise


def upsert_name_fact_from_query(session_id: str, query: str) -> dict[str, Any]:
    name = _extract_name_fact(query)
    if not name:
        return _get_or_create_session_state(session_id)
    return _merge_session_facts(session_id, {"user_name": name})

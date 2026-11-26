"""Recommend-turn orchestration: agent invocation, HITL handling, response shaping.

This sits between the route handlers (which deal with HTTP and SSE concerns)
and the agent graph (which produces the underlying result). Pure async; no
FastAPI-specific imports beyond response models.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from langgraph.types import Command

from software_recommend_system.execution_control import (
    RunCapacityExceeded,
    RunStopped,
    SessionRunConflict,
)
from software_recommend_system.logging_utils import error_fields, log_event, log_exception
from software_recommend_system.observability import traceable

from ..models import RecommendationResponse
from ..session_store import (
    _append_session_message_once,
    _get_or_create_session_state,
    _merge_session_facts,
    _safe_write_turn_memories,
)
from ..stream_utils import (
    _iter_custom_events,
    _iter_update_nodes,
    _merge_stream_updates,
    _sse,
    _to_stream_mode_chunk,
    run_agent_async,
    run_agent_stream_async,
)
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
        status="incomplete" if _get_value(result, "evidence_incomplete", False) else "success",
        stop_reason=(_get_value(result, "dynamic_stop_reason", "") or "insufficient_evidence")
        if _get_value(result, "evidence_incomplete", False)
        else None,
        evidence_incomplete=_get_value(result, "evidence_incomplete", False),
        route_decisions=_get_value(result, "route_decisions", []),
        final_answer=final_answer,
        candidates=candidates,
        mode=mode,
        iteration_count=_get_value(result, "iteration_count", 0),
        coverage=coverage,
        session_id=session_id,
        awaiting_human_confirmation=False,
        run_id=(_get_value(result, "_execution", {}) or {}).get("run_id"),
        elapsed_ms=(_get_value(result, "_execution", {}) or {}).get("elapsed_ms"),
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
    agent: Any | None = None,
) -> RecommendationResponse:
    try:
        result = await run_agent_async(agent if agent is not None else AGENT, graph_input, config=config)
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
        if interrupt_payload and interrupt_payload.get("type") == "awaiting_input":
            pending = interrupt_payload.get("pending_input") or {}
            return RecommendationResponse(
                status="awaiting_input",
                final_answer=str(pending.get("prompt", "")),
                session_id=session_id,
                pending_input=pending,
                route_decisions=_get_value(result, "route_decisions", []),
                retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
                retrieved_images=eval_payload["retrieved_images"],
            )
        if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
            pending_sub_questions = _normalize_pending_sub_questions(interrupt_payload.get("sub_questions", []))
            if not pending_sub_questions:
                logger.error(
                    "HITL_ACK_ABORT session_id=%s reason=empty_sub_questions source=%s",
                    session_id,
                    interrupt_source,
                )
                raise RuntimeError("Interrupted for human confirmation but no sub-questions were produced.")
            ack_message = _build_human_confirmation_ack(pending_sub_questions)
            logger.warning(
                "HITL_ACK_RETURN session_id=%s pending_count=%d source=%s",
                session_id,
                len(pending_sub_questions),
                interrupt_source,
            )
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", ack_message)
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
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", final_answer)
            response = _build_success_response(
                session_id=session_id,
                final_answer=final_answer,
                mode=_get_value(result, "mode", ""),
                coverage=_get_value(result, "coverage", 0.0),
                result=result,
                skill_planner_payload=skill_planner_payload,
                eval_payload=eval_payload,
            )
            await asyncio.to_thread(_write_response_memories, session_id, request_query, response)
            return response

        if fallback_answer:
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", fallback_answer)
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
            await asyncio.to_thread(_write_response_memories, session_id, request_query, response)
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
        await asyncio.to_thread(_write_response_memories, session_id, request_query, response)
        return response
    except RunStopped as exc:
        logger.warning(
            "RECOMMEND_TURN_STOPPED session_id=%s source=%s run_id=%s reason=%s elapsed_ms=%d",
            session_id,
            interrupt_source,
            exc.run_id,
            exc.stop_reason,
            exc.elapsed_ms,
        )
        await asyncio.to_thread(
            _append_session_message_once, session_id, "system", f"Request stopped: {exc.stop_reason}."
        )
        return RecommendationResponse(
            status="failed",
            final_answer=f"Request stopped: {exc.stop_reason}.",
            candidates=[],
            session_id=session_id,
            run_id=exc.run_id,
            stop_reason=exc.stop_reason,
            elapsed_ms=exc.elapsed_ms,
        )
    except (RunCapacityExceeded, SessionRunConflict) as exc:
        stop_reason = "capacity_exceeded" if isinstance(exc, RunCapacityExceeded) else "session_conflict"
        return RecommendationResponse(
            status="failed",
            final_answer="Request could not be started. Please retry shortly.",
            candidates=[],
            session_id=session_id,
            stop_reason=stop_reason,
        )
    except HTTPException:
        raise
    except Exception:
        log_exception(logger, "RECOMMEND_TURN_FAILED", session_id=session_id, source=interrupt_source)
        await asyncio.to_thread(
            _append_session_message_once, session_id, "system", "Request failed while processing this turn."
        )
        return RecommendationResponse(
            status="failed",
            final_answer="Request failed while processing this turn.",
            candidates=[],
            session_id=session_id,
            stop_reason="execution_error",
        )


def _write_response_memories(session_id: str, query: str, response: RecommendationResponse) -> None:
    if response.evidence_incomplete or response.status != "success" or not response.final_answer:
        return
    _safe_write_turn_memories(
        session_id=session_id,
        request_query=query,
        final_answer=response.final_answer,
        selected_skill=response.selected_skill,
        retrieved_doc_ids=list(response.retrieved_doc_ids or []),
    )


async def stream_recommend_turn(
    *,
    agent: Any,
    session_id: str,
    request_query: str,
    graph_input: Any,
    config: dict[str, Any],
) -> AsyncIterator[str]:
    """Shared real streaming path for all checkpoint resumes, including nested pauses."""
    merged: dict[str, Any] = {}
    tokens: list[str] = []
    execution: dict[str, Any] = {}
    try:
        if isinstance(graph_input, Command):
            snapshot = await asyncio.to_thread(agent.get_state, config)
            if isinstance(getattr(snapshot, "values", None), dict):
                merged.update(snapshot.values)
        yield _sse("meta", {"session_id": session_id, "conversation_id": session_id})
        async for item in run_agent_stream_async(agent, graph_input, config=config, stream_mode=["updates", "custom"]):
            mode, chunk = _to_stream_mode_chunk(item)
            if mode == "__execution__":
                execution = dict(chunk or {})
            elif mode == "updates":
                _merge_stream_updates(merged, chunk)
                for node_name, _payload in _iter_update_nodes(chunk):
                    yield _sse("node", {"node": node_name, "status": "end", "session_id": session_id})
            elif mode == "custom":
                for event in _iter_custom_events(chunk):
                    event_type = str(event.get("type") or "state").lower()
                    # Terminal frames are emitted only once, after the checkpoint/budget is saved.
                    if event_type in {"awaiting_input", "awaiting_confirmation", "final"}:
                        continue
                    if event_type == "token":
                        delta = str(event.get("delta") or "")
                        if not delta:
                            continue
                        tokens.append(delta)
                    yield _sse(event_type, {**event, "session_id": session_id})
        interrupt = _extract_interrupt_payload(merged)
        if interrupt and interrupt.get("type") == "awaiting_input":
            pending = interrupt.get("pending_input") or {}
            yield _sse(
                "awaiting_input",
                {
                    "status": "awaiting_input",
                    "session_id": session_id,
                    "pending_input": pending,
                    "final_answer": str(pending.get("prompt") or ""),
                    **execution,
                },
            )
            return
        if interrupt and interrupt.get("type") == "human_confirmation":
            pending = _normalize_pending_sub_questions(interrupt.get("sub_questions", []))
            if not pending:
                raise ValueError("Empty confirmation interrupt")
            prompt = _build_human_confirmation_ack(pending)
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", prompt)
            yield _sse(
                "awaiting_confirmation",
                {
                    "status": "awaiting_human_confirmation",
                    "session_id": session_id,
                    "sub_questions": pending,
                    "pending_sub_questions": pending,
                    "awaiting_human_confirmation": True,
                    "final_answer": prompt,
                    **execution,
                },
            )
            return
        final_answer = str(merged.get("final_answer") or "".join(tokens))
        merged["_execution"] = execution
        response = _build_success_response(
            session_id=session_id,
            final_answer=final_answer,
            mode=merged.get("mode", "rag"),
            coverage=merged.get("coverage", 0),
            result=merged,
            skill_planner_payload=_extract_skill_planner_payload(merged),
            eval_payload=_extract_eval_payload(merged),
        )
        if final_answer:
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", final_answer)
        await asyncio.to_thread(_write_response_memories, session_id, request_query, response)
        yield _sse("final", response.model_dump())
    except RunStopped as exc:
        yield _sse(
            "error",
            {
                "session_id": session_id,
                "run_id": exc.run_id,
                "stop_reason": exc.stop_reason,
                "elapsed_ms": exc.elapsed_ms,
                "message": f"Request stopped: {exc.stop_reason}.",
            },
        )
    except (RunCapacityExceeded, SessionRunConflict) as exc:
        reason = "capacity_exceeded" if isinstance(exc, RunCapacityExceeded) else "session_conflict"
        yield _sse("error", {"session_id": session_id, "stop_reason": reason, "message": "Request could not start."})
    except HTTPException as exc:
        yield _sse("error", {"session_id": session_id, "status_code": exc.status_code, "message": str(exc.detail)})
    except Exception:
        log_exception(logger, "RESUME_STREAM_FAILED", session_id=session_id)
        yield _sse("error", {"session_id": session_id, "message": "Request failed while resuming this task."})


def _log_recommend_task_outcome(task: asyncio.Task, session_id: str, source: str) -> None:
    if task.cancelled():
        logger.warning("RECOMMEND_TASK_CANCELLED session_id=%s source=%s", session_id, source)
        return
    exc = task.exception()
    if exc:
        log_event(
            logger, logging.ERROR, "RECOMMEND_TASK_FAILED", session_id=session_id, source=source, **error_fields(exc)
        )


def create_recommend_task(coro: Any, session_id: str, source: str) -> asyncio.Task:
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda done_task: _log_recommend_task_outcome(done_task, session_id, source))
    return task


async def await_recommend_task(
    task: asyncio.Task,
    session_id: str,
    source: str,
) -> RecommendationResponse:
    try:
        return await task
    except asyncio.CancelledError:
        logger.warning("CLIENT_DISCONNECTED_CANCELLED session_id=%s source=%s", session_id, source)
        raise


def upsert_name_fact_from_query(session_id: str, query: str) -> dict[str, Any]:
    name = _extract_name_fact(query)
    if not name:
        return _get_or_create_session_state(session_id)
    return _merge_session_facts(session_id, {"user_name": name})

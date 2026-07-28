import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.types import Command

from app.api.v1.auth import require_admin_api_key, require_api_key
from app.api.v1.models import (
    RecommendationConfirmRequest,
    RecommendationRequest,
    RecommendationResponse,
    SessionStateResponse,
    SessionStateUpdateRequest,
)
from app.core.config import settings
from software_recommend_system.multimodal import (
    IMAGE_MEDIA_TYPE_BY_SUFFIX,
    MultimodalInputError,
    VisionProcessingError,
    build_multimodal_query,
    build_persisted_user_message,
    describe_image_attachments,
)
from software_recommend_system.state import AgentState
from software_recommend_system.utils import initialize_vector_store

from .handlers._agent import AGENT
from .handlers.normalizers import (
    _build_human_confirmation_ack,
    _extract_eval_payload,
    _extract_interrupt_payload,
    _extract_skill_planner_payload,
    _get_value,
    _is_asking_user_name,
    _is_confirmation_short_query,
    _iter_awaiting_confirmation_sse,
    _normalize_candidates,
    _normalize_hitl_payload,
    _normalize_pending_sub_questions,
)
from .handlers.recommend_orchestrator import (
    await_recommend_task as _await_recommend_task,
)
from .handlers.recommend_orchestrator import (
    create_recommend_task as _create_recommend_task,
)
from .handlers.recommend_orchestrator import (
    execute_recommend_turn as _execute_recommend_turn,
)
from .handlers.recommend_orchestrator import (
    upsert_name_fact_from_query as _upsert_name_fact_from_query,
)
from .session_store import (
    _append_session_message_once,
    _append_session_messages,
    _get_or_create_session_state,
    _merge_session_facts,
    _normalize_session_messages,
    _safe_build_memory_context,
    _safe_write_turn_memories,
)
from .stream_utils import (
    _iter_custom_events,
    _iter_update_nodes,
    _merge_stream_updates,
    _sse,
    _to_stream_mode_chunk,
    run_agent_async,
    run_agent_stream_async,
)

router = APIRouter(dependencies=[Depends(require_api_key)])
admin_router = APIRouter(dependencies=[Depends(require_admin_api_key)])
logger = logging.getLogger(__name__)


async def _prepare_multimodal_request(
    request_data: RecommendationRequest,
) -> tuple[str, str, list[dict[str, str]]]:
    query = request_data.query.strip()
    image_descriptions = await asyncio.to_thread(
        describe_image_attachments,
        list(request_data.images or []),
        user_query=query,
    )
    effective_query = build_multimodal_query(query, image_descriptions)
    persisted_message = build_persisted_user_message(query, image_descriptions)
    return effective_query, persisted_message, image_descriptions


def _resolve_ingested_asset(asset_path: str) -> tuple[Path, str]:
    root = Path(settings.INGEST_PATH).expanduser().resolve()
    candidate = (root / asset_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc

    media_type = IMAGE_MEDIA_TYPE_BY_SUFFIX.get(candidate.suffix.lower())
    if not media_type or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return candidate, media_type


@router.get("/assets/{asset_path:path}")
async def get_ingested_asset(asset_path: str):
    file_path, media_type = _resolve_ingested_asset(asset_path)
    return FileResponse(file_path, media_type=media_type)


@router.post("/recommend", response_model=RecommendationResponse)
async def get_software_recommendation(request_data: RecommendationRequest):
    try:
        effective_query, persisted_user_message, image_descriptions = (
            await _prepare_multimodal_request(request_data)
        )
        session_id = request_data.session_id or uuid4().hex
        session_state = _upsert_name_fact_from_query(session_id, request_data.query)
        known_name = (session_state.get("facts") or {}).get("user_name")
        session_messages = _normalize_session_messages(session_state.get("messages", []))

        logger.info(
            "recommend request: session_id=%s timeout=%s max_iterations=%s images=%s query=%r",
            session_id,
            request_data.timeout,
            request_data.max_iterations,
            len(image_descriptions),
            request_data.query,
        )

        # Persist user message before workflow execution so disconnected clients can recover.
        _append_session_messages(session_id, [{"role": "user", "content": persisted_user_message}])

        if not image_descriptions and known_name and _is_asking_user_name(request_data.query):
            memory_answer = f"你叫{known_name}。"
            _append_session_message_once(session_id, "assistant", memory_answer)
            _safe_write_turn_memories(
                session_id=session_id,
                request_query=request_data.query,
                final_answer=memory_answer,
                selected_skill=None,
                retrieved_doc_ids=[],
            )
            return RecommendationResponse(
                status="success",
                final_answer=memory_answer,
                candidates=[],
                mode="direct",
                iteration_count=0,
                coverage=1.0,
                session_id=session_id,
                awaiting_human_confirmation=False,
                retrieval_records=[],
                retrieved_doc_ids=[],
                retrieved_images=[],
                hitl=_normalize_hitl_payload(
                    {
                        "policy": request_data.hitl_policy or "human",
                        "decision": "memory_answer",
                        "edited_subqueries": [],
                    }
                ),
            )

        if not image_descriptions and _is_confirmation_short_query(request_data.query):
            logger.warning(
                "HITL_CONFIRM_SHORTCUT session_id=%s query=%r",
                session_id,
                request_data.query,
            )
            config = {"configurable": {"thread_id": session_id}}
            resume_payload = {
                "action": "confirm",
                "sub_questions": [],
                "comment": request_data.query,
            }
            task = _create_recommend_task(
                _execute_recommend_turn(
                    session_id=session_id,
                    request_query=request_data.query,
                    graph_input=Command(resume=resume_payload),
                    config=config,
                    fallback_answer="当前没有待确认任务，请直接提交新的需求问题。",
                    fallback_mode="direct",
                    interrupt_source="confirm_shortcut",
                ),
                session_id=session_id,
                source="confirm_shortcut",
            )
            return await _await_recommend_task(task, session_id, "confirm_shortcut")

        if known_name:
            effective_query = (
                f"{effective_query}\n\n"
                "[Known User Facts]\n"
                f"user_name: {known_name}\n"
                "If user asks identity-related questions, trust this fact."
            )

        memory_seed_messages = list(session_messages)
        memory_seed_messages.append({"role": "user", "content": persisted_user_message})
        memory_context = _safe_build_memory_context(
            session_id=session_id,
            query=effective_query,
            messages=memory_seed_messages,
            facts=dict(session_state.get("facts", {}) or {}),
        )

        state = AgentState(
            user_query=effective_query,
            messages=session_messages,
            input_images=image_descriptions,
            memory_context=memory_context,
            timeout_budget=request_data.timeout,
            max_iterations=request_data.max_iterations,
            start_time=time.time(),
            hitl_policy=request_data.hitl_policy or "human",
            oracle_edits=request_data.oracle_edits or [],
            session_id=session_id,
        )

        config = {"configurable": {"thread_id": session_id}}
        task = _create_recommend_task(
            _execute_recommend_turn(
                session_id=session_id,
                request_query=persisted_user_message,
                graph_input=state,
                config=config,
                interrupt_source="recommend",
            ),
            session_id=session_id,
            source="recommend",
        )
        return await _await_recommend_task(task, session_id, "recommend")
    except MultimodalInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VisionProcessingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {exc}",
        ) from exc


@router.post("/recommend/stream")
async def stream_software_recommendation(request_data: RecommendationRequest):
    async def event_gen() -> AsyncIterator[str]:
        session_id = request_data.session_id or uuid4().hex
        try:
            effective_query, persisted_user_message, image_descriptions = (
                await _prepare_multimodal_request(request_data)
            )
            session_state = _upsert_name_fact_from_query(session_id, request_data.query)
            known_name = (session_state.get("facts") or {}).get("user_name")
            session_messages = _normalize_session_messages(session_state.get("messages", []))

            logger.info(
                "recommend stream request: session_id=%s timeout=%s max_iterations=%s images=%s query=%r",
                session_id,
                request_data.timeout,
                request_data.max_iterations,
                len(image_descriptions),
                request_data.query,
            )

            # Persist user message before workflow execution so disconnected clients can recover.
            _append_session_messages(
                session_id,
                [{"role": "user", "content": persisted_user_message}],
            )

            yield _sse(
                "meta",
                {
                    "session_id": session_id,
                    "conversation_id": session_id,
                },
            )

            if not image_descriptions and known_name and _is_asking_user_name(request_data.query):
                memory_answer = f"你叫{known_name}。"
                _append_session_message_once(session_id, "assistant", memory_answer)
                _safe_write_turn_memories(
                    session_id=session_id,
                    request_query=request_data.query,
                    final_answer=memory_answer,
                    selected_skill=None,
                    retrieved_doc_ids=[],
                )
                yield _sse(
                    "final",
                    {
                        "status": "success",
                        "session_id": session_id,
                        "final_answer": memory_answer,
                        "retrieved_doc_ids": [],
                        "retrieved_images": [],
                    },
                )
                return

            if not image_descriptions and _is_confirmation_short_query(request_data.query):
                config = {"configurable": {"thread_id": session_id}}
                resume_payload = {
                    "action": "confirm",
                    "sub_questions": [],
                    "comment": request_data.query,
                }
                response = await _execute_recommend_turn(
                    session_id=session_id,
                    request_query=request_data.query,
                    graph_input=Command(resume=resume_payload),
                    config=config,
                    fallback_answer="当前没有待确认任务，请直接提交新的需求问题。",
                    fallback_mode="direct",
                    interrupt_source="confirm_shortcut_stream",
                )
                if response.awaiting_human_confirmation:
                    async for frame in _iter_awaiting_confirmation_sse(
                        session_id,
                        response.pending_sub_questions or [],
                    ):
                        yield frame
                else:
                    yield _sse(
                        "final",
                        {
                            "status": response.status,
                            "session_id": session_id,
                            "final_answer": response.final_answer,
                            "retrieved_doc_ids": response.retrieved_doc_ids or [],
                            "retrieved_images": [
                                item.model_dump()
                                for item in (response.retrieved_images or [])
                            ],
                        },
                    )
                return

            if known_name:
                effective_query = (
                    f"{effective_query}\n\n"
                    "[Known User Facts]\n"
                    f"user_name: {known_name}\n"
                    "If user asks identity-related questions, trust this fact."
                )

            memory_seed_messages = list(session_messages)
            memory_seed_messages.append({"role": "user", "content": persisted_user_message})
            memory_context = _safe_build_memory_context(
                session_id=session_id,
                query=effective_query,
                messages=memory_seed_messages,
                facts=dict(session_state.get("facts", {}) or {}),
            )

            state = AgentState(
                user_query=effective_query,
                messages=session_messages,
                input_images=image_descriptions,
                memory_context=memory_context,
                timeout_budget=request_data.timeout,
                max_iterations=request_data.max_iterations,
                start_time=time.time(),
                hitl_policy=request_data.hitl_policy or "human",
                oracle_edits=request_data.oracle_edits or [],
                session_id=session_id,
            )
            config = {"configurable": {"thread_id": session_id}}

            merged_result: dict[str, Any] = {}
            token_parts: list[str] = []
            awaiting_emitted = False

            async for stream_item in run_agent_stream_async(
                AGENT,
                state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = _to_stream_mode_chunk(stream_item)

                if mode == "updates":
                    _merge_stream_updates(merged_result, chunk)
                    for node_name, payload in _iter_update_nodes(chunk):
                        node_payload: dict[str, Any] = {
                            "node": node_name,
                            "status": "end",
                        }
                        if isinstance(payload, dict):
                            keys = [
                                str(key)
                                for key in payload
                                if not str(key).startswith("__")
                            ]
                            if keys:
                                node_payload["payload_keys"] = keys[:12]
                        yield _sse("node", node_payload)

                    interrupt_payload = _extract_interrupt_payload(chunk)
                    if (
                        not awaiting_emitted
                        and interrupt_payload
                        and interrupt_payload.get("type") == "human_confirmation"
                    ):
                        pending_sub_questions = _normalize_pending_sub_questions(
                            interrupt_payload.get("sub_questions", [])
                        )
                        if pending_sub_questions:
                            awaiting_emitted = True
                            async for frame in _iter_awaiting_confirmation_sse(
                                session_id,
                                pending_sub_questions,
                            ):
                                yield frame
                elif mode == "custom":
                    for custom_event in _iter_custom_events(chunk):
                        event_type = str(custom_event.get("type", "state") or "state").strip()
                        if not event_type:
                            event_type = "state"
                        normalized_event_type = event_type.lower()
                        if normalized_event_type == "token":
                            delta = str(custom_event.get("delta", "") or "")
                            if not delta:
                                continue
                            token_parts.append(delta)
                            yield _sse(
                                "token",
                                {
                                    "delta": delta,
                                    "node": str(custom_event.get("node", "") or ""),
                                },
                            )
                            continue

                        yield _sse(normalized_event_type, custom_event)
                        if normalized_event_type == "awaiting_confirmation":
                            awaiting_emitted = True

            interrupt_payload = _extract_interrupt_payload(merged_result)
            if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
                if not awaiting_emitted:
                    pending_sub_questions = _normalize_pending_sub_questions(
                        interrupt_payload.get("sub_questions", [])
                    )
                    if pending_sub_questions:
                        ack_message = _build_human_confirmation_ack(pending_sub_questions)
                        _append_session_message_once(session_id, "assistant", ack_message)
                        async for frame in _iter_awaiting_confirmation_sse(
                            session_id,
                            pending_sub_questions,
                        ):
                            yield frame
                # Stop stream on confirmation interrupt; do not emit final in this turn.
                return

            final_answer = str(merged_result.get("final_answer", "") or "")
            if not final_answer and token_parts:
                final_answer = "".join(token_parts)

            if final_answer:
                _append_session_message_once(session_id, "assistant", final_answer)

            hitl_policy = str(_get_value(state, "hitl_policy", "human") or "human").strip()
            if hitl_policy not in {"human", "auto_confirm", "oracle_edit"}:
                hitl_policy = "human"

            eval_payload = _extract_eval_payload(
                merged_result,
                hitl_fallback={
                    "policy": hitl_policy,
                    "decision": "not_applicable",
                    "edited_subqueries": [],
                },
            )
            skill_planner_payload = _extract_skill_planner_payload(merged_result)

            if final_answer:
                _safe_write_turn_memories(
                    session_id=session_id,
                    request_query=persisted_user_message,
                    final_answer=final_answer,
                    selected_skill=skill_planner_payload["selected_skill"],
                    retrieved_doc_ids=list(eval_payload["retrieved_doc_ids"] or []),
                )

            yield _sse(
                "final",
                {
                    "status": "success",
                    "session_id": session_id,
                    "final_answer": final_answer,
                    "retrieved_doc_ids": eval_payload["retrieved_doc_ids"],
                    "retrieved_images": eval_payload["retrieved_images"],
                    "selected_skill": skill_planner_payload["selected_skill"],
                    "hitl": eval_payload["hitl"],
                },
            )
        except (MultimodalInputError, VisionProcessingError) as exc:
            logger.warning(
                "RECOMMEND_STREAM_MULTIMODAL_FAILED session_id=%s error=%s",
                session_id,
                exc,
            )
            yield _sse(
                "error",
                {
                    "session_id": session_id,
                    "message": str(exc),
                },
            )
        except Exception as exc:
            logger.exception("RECOMMEND_STREAM_FAILED session_id=%s error=%s", session_id, exc)
            yield _sse(
                "error",
                {
                    "session_id": session_id,
                    "message": f"Error processing stream request: {exc}",
                },
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/recommend/confirm/stream")
async def confirm_software_recommendation_stream(request_data: RecommendationConfirmRequest):
    async def event_gen() -> AsyncIterator[str]:
        session_id = request_data.session_id
        try:
            edited_sub_questions = _normalize_pending_sub_questions(request_data.sub_questions or [])
            effective_action = request_data.action
            if effective_action == "edit" and not edited_sub_questions:
                logger.warning(
                    "HITL_EDIT_EMPTY_SUBQ_STREAM session_id=%s action=edit treated_as=confirm",
                    request_data.session_id,
                )
                effective_action = "confirm"

            logger.warning(
                "HITL_CONFIRM_STREAM_REQUEST_RECEIVED session_id=%s action=%s pending_count=%d comment=%r",
                request_data.session_id,
                effective_action,
                len(edited_sub_questions),
                request_data.comment or "",
            )

            yield _sse(
                "meta",
                {
                    "session_id": session_id,
                    "conversation_id": session_id,
                },
            )

            config = {"configurable": {"thread_id": request_data.session_id}}
            resume_payload = {
                "action": effective_action,
                "sub_questions": edited_sub_questions if effective_action == "edit" else [],
                "comment": request_data.comment or "",
            }

            merged_result: dict[str, Any] = {}
            token_parts: list[str] = []
            awaiting_emitted = False

            async for stream_item in run_agent_stream_async(
                AGENT,
                Command(resume=resume_payload),
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = _to_stream_mode_chunk(stream_item)

                if mode == "updates":
                    _merge_stream_updates(merged_result, chunk)
                    for node_name, payload in _iter_update_nodes(chunk):
                        node_payload: dict[str, Any] = {
                            "node": node_name,
                            "status": "end",
                        }
                        if isinstance(payload, dict):
                            keys = [
                                str(key)
                                for key in payload
                                if not str(key).startswith("__")
                            ]
                            if keys:
                                node_payload["payload_keys"] = keys[:12]
                        yield _sse("node", node_payload)

                    interrupt_payload = _extract_interrupt_payload(chunk)
                    if (
                        not awaiting_emitted
                        and interrupt_payload
                        and interrupt_payload.get("type") == "human_confirmation"
                    ):
                        pending_sub_questions = _normalize_pending_sub_questions(
                            interrupt_payload.get("sub_questions", [])
                        )
                        if pending_sub_questions:
                            awaiting_emitted = True
                            async for frame in _iter_awaiting_confirmation_sse(
                                session_id,
                                pending_sub_questions,
                            ):
                                yield frame
                elif mode == "custom":
                    for custom_event in _iter_custom_events(chunk):
                        event_type = str(custom_event.get("type", "state") or "state").strip()
                        if not event_type:
                            event_type = "state"
                        normalized_event_type = event_type.lower()
                        if normalized_event_type == "token":
                            delta = str(custom_event.get("delta", "") or "")
                            if not delta:
                                continue
                            token_parts.append(delta)
                            yield _sse(
                                "token",
                                {
                                    "delta": delta,
                                    "node": str(custom_event.get("node", "") or ""),
                                },
                            )
                            continue

                        yield _sse(normalized_event_type, custom_event)
                        if normalized_event_type == "awaiting_confirmation":
                            awaiting_emitted = True

            interrupt_payload = _extract_interrupt_payload(merged_result)

            # Defensive loop breaker: confirm should consume the pending interrupt and continue.
            if effective_action == "confirm":
                for retry in range(2):
                    if not (interrupt_payload and interrupt_payload.get("type") == "human_confirmation"):
                        break
                    logger.warning(
                        "HITL_CONFIRM_STREAM_REINTERRUPT session_id=%s retry=%d",
                        request_data.session_id,
                        retry + 1,
                    )
                    retry_result = await run_agent_async(
                        AGENT,
                        Command(resume=resume_payload),
                        config=config,
                    )
                    if isinstance(retry_result, dict):
                        merged_result = retry_result
                    interrupt_payload = _extract_interrupt_payload(retry_result)

            eval_payload = _extract_eval_payload(
                merged_result,
                hitl_fallback={
                    "policy": "human",
                    "decision": effective_action,
                    "edited_subqueries": edited_sub_questions if effective_action == "edit" else [],
                },
            )
            skill_planner_payload = _extract_skill_planner_payload(merged_result)

            if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
                if effective_action == "confirm":
                    logger.error(
                        "HITL_CONFIRM_STREAM_LOOP_DETECTED session_id=%s payload=%s",
                        request_data.session_id,
                        interrupt_payload,
                    )
                    raise RuntimeError(
                        "Human confirmation loop detected: confirm action re-entered interrupt."
                    )

                pending_sub_questions = _normalize_pending_sub_questions(
                    interrupt_payload.get("sub_questions", [])
                )
                if not pending_sub_questions:
                    logger.error(
                        "HITL_ACK_STREAM_ABORT session_id=%s reason=empty_sub_questions payload=%s",
                        request_data.session_id,
                        interrupt_payload,
                    )
                    raise RuntimeError(
                        "Interrupted for human confirmation but no sub-questions were produced."
                    )

                if not awaiting_emitted:
                    async for frame in _iter_awaiting_confirmation_sse(
                        session_id,
                        pending_sub_questions,
                    ):
                        yield frame
                ack_message = _build_human_confirmation_ack(pending_sub_questions)
                _append_session_message_once(session_id, "assistant", ack_message)
                return

            final_answer = str(_get_value(merged_result, "final_answer", "") or "")
            if not final_answer and token_parts:
                final_answer = "".join(token_parts)

            if final_answer:
                _append_session_message_once(
                    request_data.session_id,
                    "assistant",
                    final_answer,
                )

            _safe_write_turn_memories(
                session_id=request_data.session_id,
                request_query=request_data.comment or "confirm",
                final_answer=final_answer,
                selected_skill=skill_planner_payload["selected_skill"],
                retrieved_doc_ids=list(eval_payload["retrieved_doc_ids"] or []),
            )

            yield _sse(
                "final",
                {
                    "status": "success",
                    "session_id": session_id,
                    "final_answer": final_answer,
                    "retrieved_doc_ids": eval_payload["retrieved_doc_ids"],
                    "retrieved_images": eval_payload["retrieved_images"],
                    "selected_skill": skill_planner_payload["selected_skill"],
                    "hitl": eval_payload["hitl"],
                },
            )
        except Exception as exc:
            logger.exception(
                "HITL_CONFIRM_STREAM_REQUEST_FAILED session_id=%s action=%s error=%s",
                getattr(request_data, "session_id", None),
                getattr(request_data, "action", None),
                exc,
            )
            yield _sse(
                "error",
                {
                    "session_id": session_id,
                    "message": f"Error processing confirm stream request: {exc}",
                },
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/recommend/confirm", response_model=RecommendationResponse)
async def confirm_software_recommendation(request_data: RecommendationConfirmRequest):
    try:
        edited_sub_questions = _normalize_pending_sub_questions(request_data.sub_questions or [])
        effective_action = request_data.action
        if effective_action == "edit" and not edited_sub_questions:
            logger.warning(
                "HITL_EDIT_EMPTY_SUBQ session_id=%s action=edit treated_as=confirm",
                request_data.session_id,
            )
            effective_action = "confirm"
        logger.warning(
            "HITL_CONFIRM_REQUEST_RECEIVED session_id=%s action=%s pending_count=%d comment=%r",
            request_data.session_id,
            effective_action,
            len(edited_sub_questions),
            request_data.comment or "",
        )

        config = {"configurable": {"thread_id": request_data.session_id}}

        resume_payload = {
            "action": effective_action,
            "sub_questions": edited_sub_questions if effective_action == "edit" else [],
            "comment": request_data.comment or "",
        }
        result = await run_agent_async(AGENT, Command(resume=resume_payload), config=config)
        interrupt_payload = _extract_interrupt_payload(result)

        # Defensive loop breaker: confirm should consume the pending interrupt and continue.
        if effective_action == "confirm":
            for retry in range(2):
                if not (interrupt_payload and interrupt_payload.get("type") == "human_confirmation"):
                    break
                logger.warning(
                    "HITL_CONFIRM_REINTERRUPT session_id=%s retry=%d",
                    request_data.session_id,
                    retry + 1,
                )
                result = await run_agent_async(AGENT, Command(resume=resume_payload), config=config)
                interrupt_payload = _extract_interrupt_payload(result)

        eval_payload = _extract_eval_payload(
            result,
            hitl_fallback={
                "policy": "human",
                "decision": effective_action,
                "edited_subqueries": edited_sub_questions if effective_action == "edit" else [],
            },
        )
        skill_planner_payload = _extract_skill_planner_payload(result)

        if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
            if effective_action == "confirm":
                logger.error(
                    "HITL_CONFIRM_LOOP_DETECTED session_id=%s payload=%s",
                    request_data.session_id,
                    interrupt_payload,
                )
                raise RuntimeError(
                    "Human confirmation loop detected: confirm action re-entered interrupt."
                )

            pending_sub_questions = _normalize_pending_sub_questions(
                interrupt_payload.get("sub_questions", [])
            )
            if not pending_sub_questions:
                logger.error(
                    "HITL_ACK_ABORT session_id=%s reason=empty_sub_questions payload=%s",
                    request_data.session_id,
                    interrupt_payload,
                )
                raise RuntimeError(
                    "Interrupted for human confirmation but no sub-questions were produced."
                )
            ack_message = _build_human_confirmation_ack(pending_sub_questions)
            logger.warning(
                "HITL_ACK_RETURN session_id=%s pending_count=%d",
                request_data.session_id,
                len(pending_sub_questions),
            )
            _append_session_messages(
                request_data.session_id,
                [{"role": "assistant", "content": ack_message}],
            )
            logger.warning(
                "HITL_CONFIRM_RESPONSE session_id=%s status=awaiting_human_confirmation pending_count=%d",
                request_data.session_id,
                len(pending_sub_questions),
            )
            return RecommendationResponse(
                status="awaiting_human_confirmation",
                final_answer=ack_message,
                candidates=[],
                mode="rag",
                iteration_count=0,
                coverage=0.0,
                session_id=request_data.session_id,
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
                        "policy": "human",
                        "decision": "awaiting_confirmation",
                        "edited_subqueries": [],
                    }
                ),
            )

        final_answer = _get_value(result, "final_answer", "")
        if final_answer:
            _append_session_messages(
                request_data.session_id,
                [{"role": "assistant", "content": final_answer}],
            )

        logger.warning(
            "HITL_CONFIRM_RESPONSE session_id=%s status=success final_answer_len=%d",
            request_data.session_id,
            len(final_answer or ""),
        )
        response = RecommendationResponse(
            status="success",
            final_answer=final_answer,
            candidates=_normalize_candidates(_get_value(result, "candidates", [])),
            mode=_get_value(result, "mode", ""),
            iteration_count=_get_value(result, "iteration_count", 0),
            coverage=_get_value(result, "coverage", 0.0),
            session_id=request_data.session_id,
            awaiting_human_confirmation=False,
            retrieval_records=eval_payload["retrieval_records"],
            retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
            retrieved_images=eval_payload["retrieved_images"],
            selected_skill=skill_planner_payload["selected_skill"],
            plan_steps=skill_planner_payload["plan_steps"],
            hitl=eval_payload["hitl"],
        )
        _safe_write_turn_memories(
            session_id=request_data.session_id,
            request_query=request_data.comment or "confirm",
            final_answer=response.final_answer,
            selected_skill=response.selected_skill,
            retrieved_doc_ids=list(response.retrieved_doc_ids or []),
        )
        return response
    except Exception as exc:
        logger.exception(
            "HITL_CONFIRM_REQUEST_FAILED session_id=%s action=%s error=%s",
            getattr(request_data, "session_id", None),
            getattr(request_data, "action", None),
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {exc}",
        ) from exc


@router.get("/session-state/{session_id}", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    state = _get_or_create_session_state(session_id)
    messages = _normalize_session_messages(state.get("messages", []))
    return SessionStateResponse(
        session_id=session_id,
        facts=state.get("facts", {}),
        messages=messages,
        updated_at=float(state.get("updated_at", time.time())),
    )


@router.put("/session-state/{session_id}", response_model=SessionStateResponse)
async def upsert_session_state(session_id: str, request_data: SessionStateUpdateRequest):
    state = _merge_session_facts(session_id, request_data.facts or {})
    messages = _normalize_session_messages(state.get("messages", []))
    return SessionStateResponse(
        session_id=session_id,
        facts=state.get("facts", {}),
        messages=messages,
        updated_at=float(state.get("updated_at", time.time())),
    )


@admin_router.post("/initialize-db")
async def initialize_database():
    """Initialize the vector store with sample documents."""
    try:
        sample_docs = [
            {
                "id": "redis-overview",
                "content": (
                    "Redis is an in-memory data structure store, used as a distributed, "
                    "in-memory key-value database, cache and message broker, with optional "
                    "durability. Redis provides data structures such as strings, hashes, lists, "
                    "sets, sorted sets with range queries, bitmaps, hyperloglogs, geospatial "
                    "indexes, and streams."
                ),
                "metadata": {
                    "source": "redis.io",
                    "published_date": "2023-01-15",
                    "author": "Redis Team",
                    "url": "https://redis.io/",
                    "source_ranking": 9.0,
                    "tags": ["cache", "database", "key-value"],
                },
            },
            {
                "id": "memcached-overview",
                "content": (
                    "Memcached is a general-purpose distributed memory caching system. It is "
                    "often used to speed up dynamic database-driven websites by caching data "
                    "and objects in RAM to reduce the number of times an external data source "
                    "must be read."
                ),
                "metadata": {
                    "source": "memcached.org",
                    "published_date": "2022-11-20",
                    "author": "Memcached Team",
                    "url": "https://memcached.org/",
                    "source_ranking": 8.0,
                    "tags": ["cache", "performance"],
                },
            },
        ]

        success = initialize_vector_store(sample_docs)
        if success:
            return {"status": "success", "message": "Database initialized successfully"}
        raise HTTPException(status_code=500, detail="Database initialization failed")
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error initializing database: {exc}",
        ) from exc


router.include_router(admin_router)

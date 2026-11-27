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
from software_recommend_system.document_schema import Document
from software_recommend_system.execution_control import RunStopped
from software_recommend_system.image_embedder import ImageEmbeddingError
from software_recommend_system.logging_utils import log_exception
from software_recommend_system.multimodal import (
    IMAGE_MEDIA_TYPE_BY_SUFFIX,
    MultimodalInputError,
    VisionProcessingError,
    build_multimodal_query,
    build_persisted_user_message,
    describe_image_attachments,
    embed_image_attachments,
)
from software_recommend_system.retrieval_channels import recall_image_vector
from software_recommend_system.state import AgentState
from software_recommend_system.utils import initialize_vector_store

from .handlers._agent import AGENT as AGENT
from .handlers.normalizers import (
    _build_human_confirmation_ack,
    _extract_eval_payload,
    _extract_interrupt_payload,
    _extract_skill_planner_payload,
    _get_value,
    _is_asking_user_name,
    _is_confirmation_short_query,
    _iter_awaiting_confirmation_sse,
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
from .routing_api import graph_routing_fields, preflight
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
    run_agent_stream_async,
)

router = APIRouter(dependencies=[Depends(require_api_key)])
admin_router = APIRouter(dependencies=[Depends(require_admin_api_key)])
logger = logging.getLogger(__name__)


async def _prepare_multimodal_request(
    request_data: RecommendationRequest,
) -> tuple[str, str, list[dict[str, str]], list[Document]]:
    query = request_data.query.strip()
    attachments = list(request_data.images or [])
    image_descriptions = await asyncio.to_thread(
        describe_image_attachments,
        attachments,
        user_query=query,
    )
    query_image_candidates: list[Document] = []
    if attachments and settings.RECALL_ENABLE_IMAGE_VECTOR:
        try:
            query_image_embeddings = await asyncio.to_thread(
                embed_image_attachments,
                attachments,
            )
            query_image_candidates = await asyncio.to_thread(
                recall_image_vector,
                query="",
                top_k=max(1, int(settings.RECALL_IMAGE_VECTOR_TOP_K)),
                query_image_embeddings=query_image_embeddings,
            )
        except ImageEmbeddingError as exc:
            logger.warning(
                "native image query embedding unavailable error_type=%s",
                type(exc).__name__,
            )
    effective_query = build_multimodal_query(query, image_descriptions)
    persisted_message = build_persisted_user_message(query, image_descriptions)
    return (
        effective_query,
        persisted_message,
        image_descriptions,
        query_image_candidates,
    )


async def _prepare_pinned_request(request_data: RecommendationRequest):
    """Freeze the published KB before any recall, charging preparation to active time."""
    from software_recommend_system.kb_versions import capture_snapshot, use_snapshot

    started = time.monotonic()
    remaining = min(request_data.timeout, request_data.execution_budget_seconds or request_data.timeout)
    kb_snapshot_id = capture_snapshot()
    try:
        with use_snapshot(kb_snapshot_id):
            prepared = await asyncio.wait_for(_prepare_multimodal_request(request_data), timeout=remaining)
    except TimeoutError as exc:
        raise HTTPException(408, "Multimodal preparation exceeded the request deadline") from exc
    request_data.execution_budget_seconds = remaining - (time.monotonic() - started)
    if request_data.execution_budget_seconds <= 0:
        raise HTTPException(408, "Multimodal preparation exceeded the request deadline")
    logger.info("recommend knowledge snapshot pinned: kb_snapshot_id=%s", kb_snapshot_id)
    return kb_snapshot_id, *prepared


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
    clarification = await preflight(request_data)
    if clarification is not None:
        return clarification
    if not request_data.images and _is_confirmation_short_query(request_data.query):
        return await _confirmation_shortcut(request_data, stream=False)
    session_id = request_data.session_id or uuid4().hex
    try:
        (
            kb_snapshot_id,
            effective_query,
            persisted_user_message,
            image_descriptions,
            query_image_candidates,
        ) = await _prepare_pinned_request(request_data)
        session_state = await asyncio.to_thread(_upsert_name_fact_from_query, session_id, request_data.query)
        known_name = (session_state.get("facts") or {}).get("user_name")
        session_messages = _normalize_session_messages(session_state.get("messages", []))

        logger.info(
            "recommend request: session_id=%s timeout=%s max_iterations=%s images=%s query_chars=%d",
            session_id,
            request_data.timeout,
            request_data.max_iterations,
            len(image_descriptions),
            len(request_data.query),
        )

        # Persist user message before workflow execution so disconnected clients can recover.
        await asyncio.to_thread(
            _append_session_messages, session_id, [{"role": "user", "content": persisted_user_message}]
        )

        if not image_descriptions and known_name and _is_asking_user_name(request_data.query):
            memory_answer = f"你叫{known_name}。"
            await asyncio.to_thread(_append_session_message_once, session_id, "assistant", memory_answer)
            await asyncio.to_thread(
                _safe_write_turn_memories,
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

        if known_name:
            effective_query = (
                f"{effective_query}\n\n"
                "[Known User Facts]\n"
                f"user_name: {known_name}\n"
                "If user asks identity-related questions, trust this fact."
            )

        memory_seed_messages = list(session_messages)
        memory_seed_messages.append({"role": "user", "content": persisted_user_message})
        memory_context = await asyncio.to_thread(
            _safe_build_memory_context,
            session_id=session_id,
            query=effective_query,
            messages=memory_seed_messages,
            facts=dict(session_state.get("facts", {}) or {}),
        )

        state = AgentState(
            **graph_routing_fields(request_data),
            kb_snapshot_id=kb_snapshot_id,
            user_query=effective_query,
            messages=session_messages,
            input_images=image_descriptions,
            query_image_candidates=query_image_candidates,
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
    except HTTPException:
        raise
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
    clarification = await preflight(request_data)
    if clarification is not None:

        async def clarification_events():
            yield _sse("routing_clarification", clarification.model_dump())

        return StreamingResponse(clarification_events(), media_type="text/event-stream")
    if not request_data.images and _is_confirmation_short_query(request_data.query):
        return await _confirmation_shortcut(request_data, stream=True)

    async def event_gen() -> AsyncIterator[str]:
        session_id = request_data.session_id or uuid4().hex
        try:
            (
                kb_snapshot_id,
                effective_query,
                persisted_user_message,
                image_descriptions,
                query_image_candidates,
            ) = await _prepare_pinned_request(request_data)
            session_state = await asyncio.to_thread(_upsert_name_fact_from_query, session_id, request_data.query)
            known_name = (session_state.get("facts") or {}).get("user_name")
            session_messages = _normalize_session_messages(session_state.get("messages", []))

            logger.info(
                "recommend stream request: session_id=%s timeout=%s max_iterations=%s images=%s query_chars=%d",
                session_id,
                request_data.timeout,
                request_data.max_iterations,
                len(image_descriptions),
                len(request_data.query),
            )

            # Persist user message before workflow execution so disconnected clients can recover.
            await asyncio.to_thread(
                _append_session_messages,
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
                await asyncio.to_thread(_append_session_message_once, session_id, "assistant", memory_answer)
                await asyncio.to_thread(
                    _safe_write_turn_memories,
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

            if known_name:
                effective_query = (
                    f"{effective_query}\n\n"
                    "[Known User Facts]\n"
                    f"user_name: {known_name}\n"
                    "If user asks identity-related questions, trust this fact."
                )

            memory_seed_messages = list(session_messages)
            memory_seed_messages.append({"role": "user", "content": persisted_user_message})
            memory_context = await asyncio.to_thread(
                _safe_build_memory_context,
                session_id=session_id,
                query=effective_query,
                messages=memory_seed_messages,
                facts=dict(session_state.get("facts", {}) or {}),
            )

            state = AgentState(
                **graph_routing_fields(request_data),
                kb_snapshot_id=kb_snapshot_id,
                user_query=effective_query,
                messages=session_messages,
                input_images=image_descriptions,
                query_image_candidates=query_image_candidates,
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
            execution_meta: dict[str, Any] = {}

            async for stream_item in run_agent_stream_async(
                AGENT,
                state,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                mode, chunk = _to_stream_mode_chunk(stream_item)

                if mode == "__execution__":
                    execution_meta = dict(chunk or {})
                    continue

                if mode == "updates":
                    _merge_stream_updates(merged_result, chunk)
                    for node_name, payload in _iter_update_nodes(chunk):
                        node_payload: dict[str, Any] = {
                            "node": node_name,
                            "status": "end",
                        }
                        if isinstance(payload, dict):
                            keys = [str(key) for key in payload if not str(key).startswith("__")]
                            if keys:
                                node_payload["payload_keys"] = keys[:12]
                        yield _sse("node", node_payload)

                elif mode == "custom":
                    for custom_event in _iter_custom_events(chunk):
                        event_type = str(custom_event.get("type", "state") or "state").strip()
                        if not event_type:
                            event_type = "state"
                        normalized_event_type = event_type.lower()
                        if normalized_event_type in {"awaiting_input", "awaiting_confirmation", "final"}:
                            continue
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
            if interrupt_payload and interrupt_payload.get("type") == "awaiting_input":
                yield _sse(
                    "awaiting_input",
                    {
                        "status": "awaiting_input",
                        "session_id": session_id,
                        "pending_input": interrupt_payload.get("pending_input"),
                    },
                )
                return
            if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
                if not awaiting_emitted:
                    pending_sub_questions = _normalize_pending_sub_questions(interrupt_payload.get("sub_questions", []))
                    if pending_sub_questions:
                        ack_message = _build_human_confirmation_ack(pending_sub_questions)
                        await asyncio.to_thread(_append_session_message_once, session_id, "assistant", ack_message)
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
                await asyncio.to_thread(_append_session_message_once, session_id, "assistant", final_answer)

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

            if final_answer and not merged_result.get("evidence_incomplete", False):
                await asyncio.to_thread(
                    _safe_write_turn_memories,
                    session_id=session_id,
                    request_query=persisted_user_message,
                    final_answer=final_answer,
                    selected_skill=skill_planner_payload["selected_skill"],
                    retrieved_doc_ids=list(eval_payload["retrieved_doc_ids"] or []),
                )

            yield _sse(
                "final",
                {
                    "status": "incomplete" if merged_result.get("evidence_incomplete", False) else "success",
                    "session_id": session_id,
                    "final_answer": final_answer,
                    "stop_reason": (merged_result.get("dynamic_stop_reason") or "insufficient_evidence")
                    if merged_result.get("evidence_incomplete", False)
                    else None,
                    "retrieved_doc_ids": eval_payload["retrieved_doc_ids"],
                    "retrieved_images": eval_payload["retrieved_images"],
                    "selected_skill": skill_planner_payload["selected_skill"],
                    "hitl": eval_payload["hitl"],
                    "evidence_incomplete": merged_result.get("evidence_incomplete", False),
                    **execution_meta,
                },
            )
        except RunStopped as exc:
            logger.warning(
                "RECOMMEND_STREAM_STOPPED session_id=%s run_id=%s reason=%s elapsed_ms=%d",
                session_id,
                exc.run_id,
                exc.stop_reason,
                exc.elapsed_ms,
            )
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
        except (MultimodalInputError, VisionProcessingError) as exc:
            logger.warning(
                "RECOMMEND_STREAM_MULTIMODAL_FAILED session_id=%s error_type=%s",
                session_id,
                type(exc).__name__,
            )
            yield _sse(
                "error",
                {
                    "session_id": session_id,
                    "message": str(exc),
                },
            )
        except Exception:
            log_exception(logger, "RECOMMEND_STREAM_FAILED", session_id=session_id)
            yield _sse(
                "error",
                {
                    "session_id": session_id,
                    "message": "Error processing stream request.",
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


async def _confirmation_shortcut(request_data: RecommendationRequest, *, stream: bool):
    if request_data.session_id:
        confirmation = RecommendationConfirmRequest(session_id=request_data.session_id, comment=request_data.query)
        try:
            if stream:
                return await confirm_software_recommendation_stream(confirmation)
            return await confirm_software_recommendation(confirmation)
        except HTTPException as exc:
            if exc.status_code != 409 or exc.detail != "No pending human confirmation":
                raise
    response = RecommendationResponse(
        status="success",
        final_answer="当前没有待确认任务，请直接提交新的需求问题。",
        mode="direct",
        session_id=request_data.session_id,
    )
    if not stream:
        return response

    async def events():
        yield _sse("final", response.model_dump())

    return StreamingResponse(events(), media_type="text/event-stream")


def _confirmation_payload(request_data: RecommendationConfirmRequest) -> dict[str, Any]:
    questions = _normalize_pending_sub_questions(request_data.sub_questions or [])
    action = request_data.action if request_data.action != "edit" or questions else "confirm"
    return {
        "action": action,
        "sub_questions": questions if action == "edit" else [],
        "comment": request_data.comment or "",
    }


@router.post("/recommend/confirm/stream")
async def confirm_software_recommendation_stream(request_data: RecommendationConfirmRequest):
    from starlette.background import BackgroundTask

    from .handlers.recommend_orchestrator import stream_recommend_turn
    from .routing_api import release_resume, reserve_resume

    _, lock = await reserve_resume(request_data.session_id, kind="confirmation")

    async def events():
        try:
            yield _sse("resume_accepted", {"session_id": request_data.session_id, "kind": "confirmation"})
            async for frame in stream_recommend_turn(
                agent=AGENT,
                session_id=request_data.session_id,
                request_query=request_data.comment or "confirm",
                graph_input=Command(resume=_confirmation_payload(request_data)),
                config={"configurable": {"thread_id": request_data.session_id}},
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


@router.post("/recommend/confirm", response_model=RecommendationResponse)
async def confirm_software_recommendation(request_data: RecommendationConfirmRequest):
    from .routing_api import release_resume, reserve_resume

    _, lock = await reserve_resume(request_data.session_id, kind="confirmation")
    try:
        return await _execute_recommend_turn(
            session_id=request_data.session_id,
            request_query=request_data.comment or "confirm",
            graph_input=Command(resume=_confirmation_payload(request_data)),
            config={"configurable": {"thread_id": request_data.session_id}},
            interrupt_source="confirmation",
            agent=AGENT,
        )
    finally:
        release_resume(lock)


@router.get("/session-state/{session_id}", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    state = await asyncio.to_thread(_get_or_create_session_state, session_id)
    messages = _normalize_session_messages(state.get("messages", []))
    return SessionStateResponse(
        session_id=session_id,
        facts=state.get("facts", {}),
        messages=messages,
        updated_at=float(state.get("updated_at", time.time())),
    )


@router.put("/session-state/{session_id}", response_model=SessionStateResponse)
async def upsert_session_state(session_id: str, request_data: SessionStateUpdateRequest):
    state = await asyncio.to_thread(_merge_session_facts, session_id, request_data.facts or {})
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

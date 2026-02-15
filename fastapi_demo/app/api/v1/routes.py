import logging
import re
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.api.v1.models import (
    RecommendationConfirmRequest,
    RecommendationRequest,
    RecommendationResponse,
    SessionStateResponse,
    SessionStateUpdateRequest,
)
from software_recommend_system.rag_agent import create_rag_with_routing_agent
from software_recommend_system.state import AgentState
from software_recommend_system.utils import initialize_vector_store

router = APIRouter()
logger = logging.getLogger(__name__)
AGENT = create_rag_with_routing_agent()

SESSION_STATE_STORE: Dict[str, Dict[str, Any]] = {}
NAME_ZH_PATTERN = re.compile(
    r"(?:(?:\u6211\u53eb|\u8bb0\u4f4f\u6211\u53eb|\u8bb0\u4f4f\u6211\u7684\u540d\u5b57\u662f|\u6211\u7684\u540d\u5b57\u662f)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}))"
)
NAME_EN_PATTERN = re.compile(r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\-' ]{0,40})")


def _get_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _extract_interrupt_payload(result: Any) -> Optional[Dict[str, Any]]:
    interrupts = None
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__")
    elif hasattr(result, "__interrupt__"):
        interrupts = getattr(result, "__interrupt__", None)
    if not interrupts:
        return None
    first = interrupts[0]
    payload = getattr(first, "value", first)
    if isinstance(payload, dict):
        return payload
    return {"type": "human_confirmation", "message": str(payload)}


def _extract_name_fact(query: str) -> Optional[str]:
    if not query:
        return None
    zh = NAME_ZH_PATTERN.search(query)
    if zh:
        return zh.group(1).strip()
    en = NAME_EN_PATTERN.search(query)
    if en:
        return en.group(1).strip()
    return None


def _is_asking_user_name(query: str) -> bool:
    if not query:
        return False
    normalized = query.strip().lower()
    return (
        ("\u6211\u53eb\u4ec0\u4e48" in normalized)
        or ("\u6211\u7684\u540d\u5b57" in normalized)
        or ("what is my name" in normalized)
        or ("who am i" in normalized)
    )


def _get_or_create_session_state(session_id: str) -> Dict[str, Any]:
    existing = SESSION_STATE_STORE.get(session_id)
    if existing:
        return existing
    created = {"facts": {}, "updated_at": time.time()}
    SESSION_STATE_STORE[session_id] = created
    return created


def _merge_session_facts(session_id: str, facts: Dict[str, str]) -> Dict[str, Any]:
    state = _get_or_create_session_state(session_id)
    current_facts: Dict[str, str] = state.setdefault("facts", {})
    for key, value in (facts or {}).items():
        fact_key = (key or "").strip()
        fact_value = (value or "").strip()
        if fact_key and fact_value:
            current_facts[fact_key] = fact_value
    state["updated_at"] = time.time()
    return state


def _upsert_name_fact_from_query(session_id: str, query: str) -> Dict[str, Any]:
    name = _extract_name_fact(query)
    if not name:
        return _get_or_create_session_state(session_id)
    return _merge_session_facts(session_id, {"user_name": name})


@router.post("/recommend", response_model=RecommendationResponse)
async def get_software_recommendation(request_data: RecommendationRequest):
    try:
        session_id = request_data.session_id or uuid4().hex
        session_state = _upsert_name_fact_from_query(session_id, request_data.query)
        known_name = (session_state.get("facts") or {}).get("user_name")

        logger.info(
            "recommend request: session_id=%s timeout=%s max_iterations=%s query=%r",
            session_id,
            request_data.timeout,
            request_data.max_iterations,
            request_data.query,
        )

        if known_name and _is_asking_user_name(request_data.query):
            return RecommendationResponse(
                status="success",
                final_answer=f"\u4f60\u53eb{known_name}\u3002",
                candidates=[],
                mode="chat",
                iteration_count=0,
                coverage=1.0,
                session_id=session_id,
                awaiting_human_confirmation=False,
            )

        effective_query = request_data.query
        if known_name:
            effective_query = (
                f"{request_data.query}\n\n"
                "[Known User Facts]\n"
                f"user_name: {known_name}\n"
                "If user asks identity-related questions, trust this fact."
            )

        state = AgentState(
            user_query=effective_query,
            timeout_budget=request_data.timeout,
            max_iterations=request_data.max_iterations,
            start_time=time.time(),
            session_id=session_id,
        )

        config = {"configurable": {"thread_id": session_id}}
        result = await run_agent_async(AGENT, state, config=config)
        interrupt_payload = _extract_interrupt_payload(result)
        if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
            return RecommendationResponse(
                status="awaiting_human_confirmation",
                final_answer="",
                candidates=[],
                mode="rag",
                iteration_count=0,
                coverage=0.0,
                session_id=session_id,
                awaiting_human_confirmation=True,
                pending_sub_questions=interrupt_payload.get("sub_questions", []),
            )

        return RecommendationResponse(
            status="success",
            final_answer=_get_value(result, "final_answer", ""),
            candidates=_get_value(result, "candidates", []),
            mode=_get_value(result, "mode", ""),
            iteration_count=_get_value(result, "iteration_count", 0),
            coverage=_get_value(result, "coverage", 0.0),
            session_id=session_id,
            awaiting_human_confirmation=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {exc}",
        ) from exc


@router.post("/recommend/confirm", response_model=RecommendationResponse)
async def confirm_software_recommendation(request_data: RecommendationConfirmRequest):
    try:
        config = {"configurable": {"thread_id": request_data.session_id}}
        resume_payload = {
            "action": request_data.action,
            "sub_questions": request_data.sub_questions or [],
            "comment": request_data.comment or "",
        }
        result = await run_agent_async(AGENT, Command(resume=resume_payload), config=config)
        interrupt_payload = _extract_interrupt_payload(result)
        if interrupt_payload and interrupt_payload.get("type") == "human_confirmation":
            return RecommendationResponse(
                status="awaiting_human_confirmation",
                final_answer="",
                candidates=[],
                mode="rag",
                iteration_count=0,
                coverage=0.0,
                session_id=request_data.session_id,
                awaiting_human_confirmation=True,
                pending_sub_questions=interrupt_payload.get("sub_questions", []),
            )

        return RecommendationResponse(
            status="success",
            final_answer=_get_value(result, "final_answer", ""),
            candidates=_get_value(result, "candidates", []),
            mode=_get_value(result, "mode", ""),
            iteration_count=_get_value(result, "iteration_count", 0),
            coverage=_get_value(result, "coverage", 0.0),
            session_id=request_data.session_id,
            awaiting_human_confirmation=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {exc}",
        ) from exc


@router.get("/session-state/{session_id}", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    state = _get_or_create_session_state(session_id)
    return SessionStateResponse(
        session_id=session_id,
        facts=state.get("facts", {}),
        updated_at=float(state.get("updated_at", time.time())),
    )


@router.put("/session-state/{session_id}", response_model=SessionStateResponse)
async def upsert_session_state(session_id: str, request_data: SessionStateUpdateRequest):
    state = _merge_session_facts(session_id, request_data.facts or {})
    return SessionStateResponse(
        session_id=session_id,
        facts=state.get("facts", {}),
        updated_at=float(state.get("updated_at", time.time())),
    )


async def run_agent_async(agent, graph_input: Any, config: Optional[Dict[str, Any]] = None):
    """Run the agent asynchronously."""
    try:
        result = await agent.ainvoke(graph_input, config=config)
        return result
    except Exception as exc:
        print(f"Error running agent: {exc}")
        raise


@router.post("/initialize-db")
async def initialize_database():
    """Initialize the vector store with sample documents."""
    try:
        sample_docs = [
            {
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
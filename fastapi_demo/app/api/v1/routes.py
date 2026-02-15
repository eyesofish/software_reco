import logging
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.api.v1.models import (
    RecommendationConfirmRequest,
    RecommendationRequest,
    RecommendationResponse,
)
from software_recommend_system.rag_agent import create_rag_with_routing_agent
from software_recommend_system.state import AgentState
from software_recommend_system.utils import initialize_vector_store

router = APIRouter()
logger = logging.getLogger(__name__)
AGENT = create_rag_with_routing_agent()


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


@router.post("/recommend", response_model=RecommendationResponse)
async def get_software_recommendation(request_data: RecommendationRequest):
    """
    Get software recommendations.
    - **query**: user query text
    - **timeout**: timeout in seconds, default 60
    - **max_iterations**: max iteration count, default 3
    """
    try:
        session_id = request_data.session_id or uuid4().hex
        logger.info(
            "recommend request: session_id=%s timeout=%s max_iterations=%s query=%r",
            session_id,
            request_data.timeout,
            request_data.max_iterations,
            request_data.query,
        )

        state = AgentState(
            user_query=request_data.query,
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
            {
                "content": (
                    "Ehcache is an open-source, standards-based cache used to boost performance, "
                    "offload your database and simplify scalability. Ehcache offers analysis "
                    "and reporting, enabling you to monitor cache activity and performance."
                ),
                "metadata": {
                    "source": "ehcache.org",
                    "published_date": "2023-03-10",
                    "author": "Terracotta Team",
                    "url": "https://www.ehcache.org/",
                    "source_ranking": 7.5,
                    "tags": ["cache", "java", "spring"],
                },
            },
            {
                "content": (
                    "Spring Boot is an open-source Java-based framework used to create stand-alone, "
                    "production-grade Spring applications with minimum configurations. It simplifies "
                    "the development process by providing default configurations."
                ),
                "metadata": {
                    "source": "spring.io",
                    "published_date": "2023-02-01",
                    "author": "Pivotal Team",
                    "url": "https://spring.io/projects/spring-boot",
                    "source_ranking": 9.5,
                    "tags": ["framework", "java", "spring"],
                },
            },
            {
                "content": (
                    "Hibernate is an object-relational mapping tool for the Java programming language. "
                    "It provides a framework for mapping an object-oriented domain model to a relational "
                    "database and offers data query and retrieval facilities."
                ),
                "metadata": {
                    "source": "hibernate.org",
                    "published_date": "2023-01-20",
                    "author": "Hibernate Team",
                    "url": "https://hibernate.org/",
                    "source_ranking": 8.5,
                    "tags": ["orm", "java", "database"],
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

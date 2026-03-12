import logging
import json
import ast
import os
import re
import time
import asyncio
from pathlib import Path
from threading import RLock
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app.api.v1.models import (
    RecommendationConfirmRequest,
    RecommendationRequest,
    RecommendationResponse,
    SessionStateResponse,
    SessionStateUpdateRequest,
)
from software_recommend_system.rag_agent import create_rag_with_routing_agent
from software_recommend_system.observability import traceable
from software_recommend_system.state import AgentState
from software_recommend_system.config import settings as agent_settings
from software_recommend_system.memory_retriever import build_memory_context
from software_recommend_system.memory_store import write_episode, write_fact, write_semantic
from software_recommend_system.utils import initialize_vector_store

router = APIRouter()
logger = logging.getLogger(__name__)
AGENT = create_rag_with_routing_agent()
NAME_ZH_PATTERN = re.compile(
    r"(?:(?:^|[锛?銆傦紒锛?\s])(?:\u6211\u53eb|\u8bb0\u4f4f\u6211\u53eb|\u8bb0\u4f4f\u6211\u7684\u540d\u5b57\u662f|\u6211\u7684\u540d\u5b57\u662f)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}))"
)
NAME_IS_ZH_PATTERN = re.compile(
    r"(?:(?:^|[锛?銆傦紒锛?\s])(?:\u6211\u662f)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}))"
)
NAME_EN_PATTERN = re.compile(r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\-' ]{0,40})")
USER_NAME_QUESTION_PATTERNS = (
    re.compile(r"\u6211\u53eb(\u4ec0\u4e48|\u5565|\u8c01)"),
    re.compile(r"\u6211\u7684\u540d\u5b57(\u662f)?(\u4ec0\u4e48|\u5565|\u8c01)"),
    re.compile(r"\u6211\u662f\u8c01"),
    re.compile(r"(?i)what is my name"),
    re.compile(r"(?i)who am i"),
)
INVALID_NAME_VALUES = {
    "\u4ec0\u4e48",
    "\u8c01",
    "\u5565",
    "\u54ea\u4f4d",
    "\u540d\u5b57",
    "\u59d3\u540d",
    "name",
    "what",
    "who",
}
CONFIRM_SHORT_QUERY_PATTERN = re.compile(
    r"(?i)^\s*(?:\u786e\u8ba4|\u7ee7\u7eed|\u7ee7\u7eed\u5427|\u597d\u7684|\u597d|ok|okay|yes|y|go on|continue)\s*[.!?\u3002\uff01\uff1f]*\s*$"
)
SESSION_STATE_FILE = Path(os.getenv("SESSION_STATE_FILE", ".runtime/fastapi_session_state.json"))
SESSION_MAX_MESSAGES = int(os.getenv("SESSION_MAX_MESSAGES", "30"))
_SESSION_LOCK = RLock()


def _layered_memory_enabled() -> bool:
    return bool(getattr(agent_settings, "FEATURE_LAYERED_MEMORY", True))


def _memory_writeback_enabled() -> bool:
    return _layered_memory_enabled() and bool(getattr(agent_settings, "MEMORY_ENABLE_WRITEBACK", True))


def _truncate_memory_text(text: str, max_chars: int = 800) -> str:
    value = str(text or "").strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "..."


def _safe_build_memory_context(
    *,
    session_id: str,
    query: str,
    messages: List[Dict[str, str]],
    facts: Dict[str, str],
) -> List[Dict[str, Any]]:
    if not _layered_memory_enabled():
        return []
    try:
        return build_memory_context(
            session_id=session_id,
            query=query,
            messages=messages,
            facts=facts,
        )
    except Exception:
        logger.exception("MEMORY_CONTEXT_BUILD_FAILED session_id=%s", session_id)
        return []


def _safe_write_fact_to_memory(session_id: str, key: str, value: str) -> None:
    if not _memory_writeback_enabled():
        return
    try:
        write_fact(session_id, key, value)
    except Exception:
        logger.exception(
            "MEMORY_FACT_WRITE_FAILED session_id=%s key=%s",
            session_id,
            key,
        )


def _safe_write_turn_memories(
    *,
    session_id: str,
    request_query: str,
    final_answer: str,
    selected_skill: Optional[str],
    retrieved_doc_ids: List[str],
) -> None:
    if not _memory_writeback_enabled():
        return
    try:
        min_chars = max(1, int(getattr(agent_settings, "MEMORY_WRITEBACK_MIN_CHARS", 24)))
        query_text = str(request_query or "").strip()
        answer_text = str(final_answer or "").strip()
        skill = str(selected_skill or "").strip()
        doc_ids = [str(item or "").strip() for item in retrieved_doc_ids if str(item or "").strip()]

        if len(query_text) >= min_chars:
            write_episode(
                session_id,
                _truncate_memory_text(f"user_query: {query_text}", max_chars=600),
                tags=["turn", "user_query"],
                salience=0.55,
            )

        if len(answer_text) >= min_chars:
            answer_tags = ["turn", "assistant_answer"]
            if skill:
                answer_tags.append(f"skill:{skill}")
            write_episode(
                session_id,
                _truncate_memory_text(f"assistant_answer: {answer_text}", max_chars=900),
                tags=answer_tags,
                salience=0.5,
            )

        if skill:
            _safe_write_fact_to_memory(session_id, "last_selected_skill", skill)

        if (
            doc_ids
            and bool(getattr(agent_settings, "MEMORY_WRITEBACK_ENABLE_SEMANTIC", True))
        ):
            short_ids = ", ".join(doc_ids[:8])
            semantic_tags = ["retrieval_evidence"]
            if skill:
                semantic_tags.append(f"skill:{skill}")
            write_semantic(
                session_id,
                f"relevant_doc_ids: {short_ids}",
                tags=semantic_tags,
                salience=0.72,
            )
    except Exception:
        logger.exception("MEMORY_TURN_WRITEBACK_FAILED session_id=%s", session_id)


def _normalize_session_facts(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        fact_key = str(key or "").strip()
        fact_value = str(value or "").strip()
        if fact_key and fact_value:
            normalized[fact_key] = fact_value
    return normalized


def _normalize_session_messages(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role in {"system", "user", "assistant"} and content:
            normalized.append({"role": role, "content": content})

    return normalized[-SESSION_MAX_MESSAGES:]


def _normalize_updated_at(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return time.time()


def _normalize_session_state(raw: Any) -> Dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    return {
        "facts": _normalize_session_facts(state.get("facts", {})),
        "messages": _normalize_session_messages(state.get("messages", [])),
        "updated_at": _normalize_updated_at(state.get("updated_at")),
    }


def _persist_session_state_store_locked() -> None:
    SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_FILE.write_text(
        json.dumps(SESSION_STATE_STORE, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_session_state_store() -> Dict[str, Dict[str, Any]]:
    if not SESSION_STATE_FILE.exists():
        return {}

    try:
        content = SESSION_STATE_FILE.read_text(encoding="utf-8")
        raw = json.loads(content)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("failed to load session state file %s: %s", SESSION_STATE_FILE, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("invalid session state payload, expected dict at root")
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for session_id, state in raw.items():
        sid = str(session_id or "").strip()
        if not sid:
            continue
        normalized[sid] = _normalize_session_state(state)

    return normalized


SESSION_STATE_STORE: Dict[str, Dict[str, Any]] = _load_session_state_store()


def _get_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _normalize_candidates(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        dumped = None
        if hasattr(item, "model_dump"):
            try:
                dumped = item.model_dump()
            except Exception:
                dumped = None
        elif hasattr(item, "dict"):
            try:
                dumped = item.dict()
            except Exception:
                dumped = None
        if isinstance(dumped, dict):
            normalized.append(dumped)
    return normalized


def _normalize_retrieved_doc_ids(raw: Any) -> List[str]:
    normalized: List[str] = []
    seen = set()
    if not isinstance(raw, (list, tuple, set)):
        return normalized
    for value in raw:
        doc_id = str(value or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        normalized.append(doc_id)
    return normalized


def _normalize_retrieval_records(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        subquery_id = str(item.get("subquery_id", "")).strip()
        subquery = str(item.get("subquery", "")).strip()
        retrieved_doc_ids = _normalize_retrieved_doc_ids(item.get("retrieved_doc_ids", []))
        retrieved_contexts_raw = item.get("retrieved_contexts", [])
        retrieved_contexts = []
        if isinstance(retrieved_contexts_raw, list):
            for context in retrieved_contexts_raw[:8]:
                text = str(context or "").strip()
                if text:
                    retrieved_contexts.append(text)
        channel_counts_raw = item.get("channel_counts", {})
        channel_counts: Dict[str, int] = {}
        if isinstance(channel_counts_raw, dict):
            for key, value in channel_counts_raw.items():
                channel = str(key or "").strip().lower()
                if not channel:
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    channel_counts[channel] = count
        channels_used = sorted(channel_counts.keys())
        selected_skill = str(item.get("selected_skill", "")).strip()
        skill_used = str(item.get("skill_used", "")).strip() or selected_skill
        rerank_mode = str(item.get("rerank_mode", "")).strip()
        normalized.append(
            {
                "subquery_id": subquery_id,
                "subquery": subquery,
                "retrieved_doc_ids": retrieved_doc_ids,
                "retrieved_contexts": retrieved_contexts,
                "channel_counts": channel_counts,
                "channels_used": channels_used,
                "selected_skill": selected_skill,
                "skill_used": skill_used,
                "rerank_mode": rerank_mode,
            }
        )
    return normalized


def _normalize_plan_steps(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            step_id = str(item.get("step_id", "") or f"step_{index}").strip() or f"step_{index}"
            objective = str(item.get("objective", "") or "").strip()
            action = str(item.get("action", "retrieve") or "retrieve").strip() or "retrieve"
            query = str(item.get("query", "") or "").strip()
            retrieval_profile = str(item.get("retrieval_profile", "balanced") or "balanced").strip() or "balanced"
            required = bool(item.get("required", True))
        else:
            step_id = f"step_{index}"
            objective = str(item or "").strip()
            action = "retrieve"
            query = objective
            retrieval_profile = "balanced"
            required = True
        if not objective and not query:
            continue
        normalized.append(
            {
                "step_id": step_id,
                "objective": objective or query,
                "action": action,
                "query": query or objective,
                "retrieval_profile": retrieval_profile,
                "required": required,
            }
        )
    return normalized


def _extract_skill_planner_payload(result: Any) -> Dict[str, Any]:
    selected_skill = str(_get_value(result, "selected_skill", "") or "").strip() or None
    plan_steps = _normalize_plan_steps(_get_value(result, "plan_steps", []))
    return {
        "selected_skill": selected_skill,
        "plan_steps": plan_steps,
    }


def _normalize_hitl_payload(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        return None
    payload = raw
    policy = str(payload.get("policy", "human") or "human").strip() or "human"
    decision = str(payload.get("decision", "confirm") or "confirm").strip() or "confirm"
    edited_subqueries = _normalize_pending_sub_questions(payload.get("edited_subqueries", []))
    return {
        "policy": policy,
        "decision": decision,
        "edited_subqueries": edited_subqueries,
    }


def _extract_eval_payload(
    result: Any,
    *,
    hitl_fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    retrieval_records = _normalize_retrieval_records(_get_value(result, "retrieval_records", []))
    retrieved_doc_ids = _normalize_retrieved_doc_ids(_get_value(result, "retrieved_doc_ids", []))

    hitl = _normalize_hitl_payload(_get_value(result, "hitl", None))
    if hitl is None:
        hitl = _normalize_hitl_payload(hitl_fallback)

    return {
        "retrieval_records": retrieval_records,
        "retrieved_doc_ids": retrieved_doc_ids,
        "hitl": hitl,
    }


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


def _normalize_pending_sub_questions(raw: Any) -> List[str]:
    normalized: List[str] = []
    seen = set()

    def append_unique(value: str) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        normalized.append(text)

    def try_parse_structured_text(text: str) -> Optional[Any]:
        stripped = text.strip()
        if not stripped or stripped[0] not in {"[", "{"}:
            return None
        try:
            return json.loads(stripped)
        except Exception:
            pass
        try:
            return ast.literal_eval(stripped)
        except Exception:
            return None

    def collect(value: Any) -> None:
        if value is None:
            return

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return
            parsed = try_parse_structured_text(text)
            if parsed is not None:
                collect(parsed)
                return
            append_unique(text)
            return

        if isinstance(value, dict):
            preferred: List[Any] = []
            for key in ("sub_questions", "pending_sub_questions"):
                if key in value:
                    preferred.append(value.get(key))
            if preferred:
                for item in preferred:
                    collect(item)
                return
            for nested in value.values():
                collect(nested)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                collect(item)
            return

        text = str(value).strip()
        if text:
            append_unique(text)

    collect(raw)
    return normalized


def _build_human_confirmation_ack(sub_questions: List[str]) -> str:
    normalized = _normalize_pending_sub_questions(sub_questions)
    if not normalized:
        raise ValueError("pending_sub_questions must not be empty for human confirmation ACK")

    preview = "\n".join(
        f"{idx + 1}. {question}" for idx, question in enumerate(normalized[:3])
    )
    suffix = "\n..." if len(normalized) > 3 else ""
    return (
        "已收到请求，当前已生成子问题，正在等待确认后继续执行。\n"
        "Status: waiting for confirmation.\n"
        "请调用 /api/v1/recommend/confirm，并使用 action=confirm 或 action=edit。\n"
        f"待确认子问题预览:\n{preview}{suffix}"
    )


def _sse(event: str, payload: Optional[Dict[str, Any]] = None) -> str:
    body = dict(payload or {})
    body.setdefault("type", event)
    return f"event: {event}\ndata: {json.dumps(body, ensure_ascii=False, default=str)}\n\n"


async def _iter_awaiting_confirmation_sse(
    session_id: str,
    sub_questions: List[str],
    *,
    delay_seconds: float = 0.12,
) -> AsyncIterator[str]:
    """Emit awaiting_confirmation incrementally so sub-questions render progressively."""
    normalized = _normalize_pending_sub_questions(sub_questions)
    if not normalized:
        return

    for idx in range(1, len(normalized) + 1):
        yield _sse(
            "awaiting_confirmation",
            {
                "session_id": session_id,
                "sub_questions": normalized[:idx],
            },
        )
        if idx < len(normalized) and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)


def _to_stream_mode_chunk(item: Any) -> Tuple[str, Any]:
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], str)
    ):
        return item[0], item[1]
    return "updates", item


def _iter_update_nodes(chunk: Any) -> List[Tuple[str, Any]]:
    if not isinstance(chunk, dict):
        return []
    entries: List[Tuple[str, Any]] = []
    for node_name, payload in chunk.items():
        node = str(node_name or "").strip()
        if not node or node.startswith("__"):
            continue
        entries.append((node, payload))
    return entries


def _iter_custom_events(chunk: Any) -> List[Dict[str, Any]]:
    if isinstance(chunk, dict):
        return [chunk]
    if isinstance(chunk, (list, tuple)):
        return [item for item in chunk if isinstance(item, dict)]
    return []


def _merge_stream_updates(result: Dict[str, Any], chunk: Any) -> None:
    if not isinstance(chunk, dict):
        return
    for node_name, payload in chunk.items():
        node = str(node_name or "").strip()
        if node == "__interrupt__":
            result["__interrupt__"] = payload
            continue
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            result[key] = value


def _is_sync_sqlite_checkpointer(checkpointer_type: str, checkpointer_module: str) -> bool:
    return (
        checkpointer_type == "SqliteSaver"
        and "langgraph.checkpoint.sqlite" in checkpointer_module
        and ".aio" not in checkpointer_module
    )


async def _stream_agent_sync_fallback(
    agent: Any,
    graph_input: Any,
    config: Optional[Dict[str, Any]] = None,
    stream_mode: Optional[List[str]] = None,
) -> AsyncIterator[Any]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def produce() -> None:
        try:
            iterator = agent.stream(
                graph_input,
                config=config,
                stream_mode=stream_mode,
            )
            for item in iterator:
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    producer_task = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await producer_task


def _extract_name_fact(query: str) -> Optional[str]:
    if not query or _is_asking_user_name(query):
        return None

    zh = NAME_ZH_PATTERN.search(query)
    if zh:
        normalized = _normalize_candidate_name(zh.group(1))
        if normalized:
            return normalized

    zh_is = NAME_IS_ZH_PATTERN.search(query)
    if zh_is:
        normalized = _normalize_candidate_name(zh_is.group(1))
        if normalized:
            return normalized

    en = NAME_EN_PATTERN.search(query)
    if en:
        normalized = _normalize_candidate_name(en.group(1))
        if normalized:
            return normalized

    return None


def _normalize_candidate_name(raw: str) -> Optional[str]:
    candidate = (raw or "").strip()
    candidate = candidate.strip(" \t\r\n,.!?;:，。！？；：“”\"'()[]（）【】")
    if not candidate:
        return None

    lowered = candidate.lower()
    if candidate in INVALID_NAME_VALUES or lowered in INVALID_NAME_VALUES:
        return None
    if candidate.endswith(("\u5417", "\u5462", "\u4e48", "\u561b")):
        return None
    return candidate


def _is_asking_user_name(query: str) -> bool:
    if not query:
        return False
    normalized = query.strip()
    for pattern in USER_NAME_QUESTION_PATTERNS:
        if pattern.search(normalized):
            return True

    lowered = normalized.lower()
    return ("what's my name" in lowered) or ("whats my name" in lowered)


def _is_confirmation_short_query(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    return CONFIRM_SHORT_QUERY_PATTERN.search(normalized) is not None


def _get_or_create_session_state(session_id: str) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")

    with _SESSION_LOCK:
        existing = SESSION_STATE_STORE.get(sid)
        if existing:
            normalized = _normalize_session_state(existing)
            SESSION_STATE_STORE[sid] = normalized
            return normalized

        created = {"facts": {}, "messages": [], "updated_at": time.time()}
        SESSION_STATE_STORE[sid] = created
        _persist_session_state_store_locked()
        return created


def _merge_session_facts(session_id: str, facts: Dict[str, str]) -> Dict[str, Any]:
    with _SESSION_LOCK:
        state = _get_or_create_session_state(session_id)
        current_facts: Dict[str, str] = state.setdefault("facts", {})
        for key, value in (facts or {}).items():
            fact_key = (key or "").strip()
            fact_value = (value or "").strip()
            if fact_key == "user_name":
                normalized_name = _normalize_candidate_name(fact_value)
                if not normalized_name:
                    continue
                fact_value = normalized_name
            if fact_key and fact_value:
                current_facts[fact_key] = fact_value
                _safe_write_fact_to_memory(session_id, fact_key, fact_value)
        state["updated_at"] = time.time()
        _persist_session_state_store_locked()
        return state


def _append_session_messages(session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    updates = _normalize_session_messages(messages)
    if not updates:
        return _get_or_create_session_state(session_id)

    with _SESSION_LOCK:
        state = _get_or_create_session_state(session_id)
        history = _normalize_session_messages(state.get("messages", []))
        history.extend(updates)
        state["messages"] = history[-SESSION_MAX_MESSAGES:]
        state["updated_at"] = time.time()
        _persist_session_state_store_locked()
        return state


def _append_session_message_once(session_id: str, role: str, content: str) -> Dict[str, Any]:
    updates = _normalize_session_messages([{"role": role, "content": content}])
    if not updates:
        return _get_or_create_session_state(session_id)

    candidate = updates[0]
    with _SESSION_LOCK:
        state = _get_or_create_session_state(session_id)
        history = _normalize_session_messages(state.get("messages", []))
        if (
            history
            and history[-1].get("role") == candidate["role"]
            and history[-1].get("content") == candidate["content"]
        ):
            logger.info(
                "SESSION_MESSAGE_DUPLICATE_SKIPPED session_id=%s role=%s",
                session_id,
                candidate["role"],
            )
            return state
        history.append(candidate)
        state["messages"] = history[-SESSION_MAX_MESSAGES:]
        state["updated_at"] = time.time()
        _persist_session_state_store_locked()
        return state


@traceable(name="api_recommend_turn")
async def _execute_recommend_turn(
    *,
    session_id: str,
    request_query: str,
    graph_input: Any,
    config: Dict[str, Any],
    fallback_answer: Optional[str] = None,
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
            response = RecommendationResponse(
                status="success",
                final_answer=final_answer,
                candidates=_normalize_candidates(_get_value(result, "candidates", [])),
                mode=_get_value(result, "mode", ""),
                iteration_count=_get_value(result, "iteration_count", 0),
                coverage=_get_value(result, "coverage", 0.0),
                session_id=session_id,
                awaiting_human_confirmation=False,
                retrieval_records=eval_payload["retrieval_records"],
                retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
                selected_skill=skill_planner_payload["selected_skill"],
                plan_steps=skill_planner_payload["plan_steps"],
                hitl=eval_payload["hitl"],
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
            response = RecommendationResponse(
                status="success",
                final_answer=fallback_answer,
                candidates=[],
                mode=fallback_mode,
                iteration_count=0,
                coverage=1.0,
                session_id=session_id,
                awaiting_human_confirmation=False,
                retrieval_records=fallback_eval_payload["retrieval_records"],
                retrieved_doc_ids=fallback_eval_payload["retrieved_doc_ids"],
                selected_skill=skill_planner_payload["selected_skill"],
                plan_steps=skill_planner_payload["plan_steps"],
                hitl=fallback_eval_payload["hitl"],
            )
            _safe_write_turn_memories(
                session_id=session_id,
                request_query=request_query,
                final_answer=response.final_answer,
                selected_skill=response.selected_skill,
                retrieved_doc_ids=list(response.retrieved_doc_ids or []),
            )
            return response

        response = RecommendationResponse(
            status="success",
            final_answer=final_answer,
            candidates=_normalize_candidates(_get_value(result, "candidates", [])),
            mode=_get_value(result, "mode", ""),
            iteration_count=_get_value(result, "iteration_count", 0),
            coverage=_get_value(result, "coverage", 0.0),
            session_id=session_id,
            awaiting_human_confirmation=False,
            retrieval_records=eval_payload["retrieval_records"],
            retrieved_doc_ids=eval_payload["retrieved_doc_ids"],
            selected_skill=skill_planner_payload["selected_skill"],
            plan_steps=skill_planner_payload["plan_steps"],
            hitl=eval_payload["hitl"],
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


def _create_recommend_task(coro: Any, session_id: str, source: str) -> asyncio.Task:
    task = asyncio.create_task(coro)
    task.add_done_callback(
        lambda done_task: _log_recommend_task_outcome(done_task, session_id, source)
    )
    return task


async def _await_recommend_task(
    task: asyncio.Task,
    session_id: str,
    source: str,
) -> RecommendationResponse:
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        logger.warning("CLIENT_DISCONNECTED_CONTINUE session_id=%s source=%s", session_id, source)
        raise


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
        session_messages = _normalize_session_messages(session_state.get("messages", []))

        logger.info(
            "recommend request: session_id=%s timeout=%s max_iterations=%s query=%r",
            session_id,
            request_data.timeout,
            request_data.max_iterations,
            request_data.query,
        )

        # Persist user message before workflow execution so disconnected clients can recover.
        _append_session_messages(session_id, [{"role": "user", "content": request_data.query}])

        if known_name and _is_asking_user_name(request_data.query):
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
                hitl=_normalize_hitl_payload(
                    {
                        "policy": request_data.hitl_policy or "human",
                        "decision": "memory_answer",
                        "edited_subqueries": [],
                    }
                ),
            )

        if _is_confirmation_short_query(request_data.query):
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

        effective_query = request_data.query
        if known_name:
            effective_query = (
                f"{request_data.query}\n\n"
                "[Known User Facts]\n"
                f"user_name: {known_name}\n"
                "If user asks identity-related questions, trust this fact."
            )

        memory_seed_messages = list(session_messages)
        memory_seed_messages.append({"role": "user", "content": request_data.query})
        memory_context = _safe_build_memory_context(
            session_id=session_id,
            query=request_data.query,
            messages=memory_seed_messages,
            facts=dict(session_state.get("facts", {}) or {}),
        )

        state = AgentState(
            user_query=effective_query,
            messages=session_messages,
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
                request_query=request_data.query,
                graph_input=state,
                config=config,
                interrupt_source="recommend",
            ),
            session_id=session_id,
            source="recommend",
        )
        return await _await_recommend_task(task, session_id, "recommend")
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
            session_state = _upsert_name_fact_from_query(session_id, request_data.query)
            known_name = (session_state.get("facts") or {}).get("user_name")
            session_messages = _normalize_session_messages(session_state.get("messages", []))

            logger.info(
                "recommend stream request: session_id=%s timeout=%s max_iterations=%s query=%r",
                session_id,
                request_data.timeout,
                request_data.max_iterations,
                request_data.query,
            )

            # Persist user message before workflow execution so disconnected clients can recover.
            _append_session_messages(session_id, [{"role": "user", "content": request_data.query}])

            yield _sse(
                "meta",
                {
                    "session_id": session_id,
                    "conversation_id": session_id,
                },
            )

            if known_name and _is_asking_user_name(request_data.query):
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
                    },
                )
                return

            if _is_confirmation_short_query(request_data.query):
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
                        },
                    )
                return

            effective_query = request_data.query
            if known_name:
                effective_query = (
                    f"{request_data.query}\n\n"
                    "[Known User Facts]\n"
                    f"user_name: {known_name}\n"
                    "If user asks identity-related questions, trust this fact."
                )

            memory_seed_messages = list(session_messages)
            memory_seed_messages.append({"role": "user", "content": request_data.query})
            memory_context = _safe_build_memory_context(
                session_id=session_id,
                query=request_data.query,
                messages=memory_seed_messages,
                facts=dict(session_state.get("facts", {}) or {}),
            )

            state = AgentState(
                user_query=effective_query,
                messages=session_messages,
                memory_context=memory_context,
                timeout_budget=request_data.timeout,
                max_iterations=request_data.max_iterations,
                start_time=time.time(),
                hitl_policy=request_data.hitl_policy or "human",
                oracle_edits=request_data.oracle_edits or [],
                session_id=session_id,
            )
            config = {"configurable": {"thread_id": session_id}}

            merged_result: Dict[str, Any] = {}
            token_parts: List[str] = []
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
                        node_payload: Dict[str, Any] = {
                            "node": node_name,
                            "status": "end",
                        }
                        if isinstance(payload, dict):
                            keys = [
                                str(key)
                                for key in payload.keys()
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
                    request_query=request_data.query,
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
                    "selected_skill": skill_planner_payload["selected_skill"],
                    "hitl": eval_payload["hitl"],
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

            merged_result: Dict[str, Any] = {}
            token_parts: List[str] = []
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
                        node_payload: Dict[str, Any] = {
                            "node": node_name,
                            "status": "end",
                        }
                        if isinstance(payload, dict):
                            keys = [
                                str(key)
                                for key in payload.keys()
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


@traceable(name="api_agent_stream")
async def run_agent_stream_async(
    agent: Any,
    graph_input: Any,
    *,
    config: Optional[Dict[str, Any]] = None,
    stream_mode: Optional[List[str]] = None,
) -> AsyncIterator[Any]:
    checkpointer = getattr(agent, "checkpointer", None)
    checkpointer_type = type(checkpointer).__name__
    checkpointer_module = getattr(type(checkpointer), "__module__", "")
    mode_list = list(stream_mode or ["updates", "custom"])

    if _is_sync_sqlite_checkpointer(checkpointer_type, checkpointer_module):
        logger.info(
            "AGENT_STREAM_START mode=sync_sqlite graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in _stream_agent_sync_fallback(
            agent,
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=sync_sqlite checkpointer=%s",
            checkpointer_type,
        )
        return

    try:
        logger.info(
            "AGENT_STREAM_START mode=async graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in agent.astream(
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=async checkpointer=%s",
            checkpointer_type,
        )
    except (TypeError, NotImplementedError) as exc:
        error_text = str(exc)
        if (
            "does not support async methods" not in error_text
            and "AsyncSqliteSaver" not in error_text
        ):
            raise
        logger.warning(
            "Agent async stream is unavailable for current checkpointer; "
            "falling back to sync stream in thread pool.",
        )
        logger.info(
            "AGENT_STREAM_START mode=sync_fallback graph_input_type=%s checkpointer=%s module=%s stream_mode=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
            mode_list,
        )
        async for item in _stream_agent_sync_fallback(
            agent,
            graph_input,
            config=config,
            stream_mode=mode_list,
        ):
            yield item
        logger.info(
            "AGENT_STREAM_DONE mode=sync_fallback checkpointer=%s",
            checkpointer_type,
        )
    except Exception:
        logger.exception(
            "AGENT_STREAM_FAILED checkpointer=%s module=%s",
            checkpointer_type,
            checkpointer_module,
        )
        raise


@traceable(name="api_agent_invoke")
async def run_agent_async(agent, graph_input: Any, config: Optional[Dict[str, Any]] = None):
    """Run the agent asynchronously."""
    checkpointer = getattr(agent, "checkpointer", None)
    checkpointer_type = type(checkpointer).__name__
    checkpointer_module = getattr(type(checkpointer), "__module__", "")

    # SqliteSaver is sync-only in some LangGraph versions; avoid ainvoke NotImplementedError loop.
    if (
        checkpointer_type == "SqliteSaver"
        and "langgraph.checkpoint.sqlite" in checkpointer_module
        and ".aio" not in checkpointer_module
    ):
        logger.info(
            "AGENT_INVOKE_START mode=sync_sqlite graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await asyncio.to_thread(agent.invoke, graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=sync_sqlite elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result

    try:
        logger.info(
            "AGENT_INVOKE_START mode=async graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await agent.ainvoke(graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=async elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result
    except (TypeError, NotImplementedError) as exc:
        # Fallback for mixed-version environments with partial async support.
        error_text = str(exc)
        if (
            "does not support async methods" not in error_text
            and "AsyncSqliteSaver" not in error_text
        ):
            raise
        logger.warning(
            "Agent async invocation is unavailable for current checkpointer; "
            "falling back to sync invoke in thread pool.",
        )
        logger.info(
            "AGENT_INVOKE_START mode=sync_fallback graph_input_type=%s checkpointer=%s module=%s",
            type(graph_input).__name__,
            checkpointer_type,
            checkpointer_module,
        )
        started_at = time.perf_counter()
        result = await asyncio.to_thread(agent.invoke, graph_input, config=config)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "AGENT_INVOKE_DONE mode=sync_fallback elapsed_ms=%d checkpointer=%s",
            elapsed_ms,
            checkpointer_type,
        )
        return result
    except Exception as exc:
        logger.exception(
            "AGENT_INVOKE_FAILED mode=unknown checkpointer=%s module=%s error=%s",
            checkpointer_type,
            checkpointer_module,
            exc,
        )
        logger.exception("Error running agent: %s", exc)
        raise


@router.post("/initialize-db")
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


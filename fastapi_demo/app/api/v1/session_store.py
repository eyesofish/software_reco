import logging
import time
from pathlib import Path
from typing import Any

from software_recommend_system.config import settings as agent_settings
from software_recommend_system.logging_utils import log_exception
from software_recommend_system.memory_retriever import build_memory_context
from software_recommend_system.memory_store import write_episode, write_fact, write_semantic
from software_recommend_system.session_rows import SessionRows, open_session_rows

logger = logging.getLogger(__name__)

SESSION_MAX_MESSAGES = agent_settings.SESSION_MAX_MESSAGES

INVALID_NAME_VALUES = {
    "什么",
    "谁",
    "啥",
    "哪位",
    "名字",
    "姓名",
    "name",
    "what",
    "who",
}


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
    messages: list[dict[str, str]],
    facts: dict[str, str],
) -> list[dict[str, Any]]:
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
        log_exception(logger, "MEMORY_CONTEXT_BUILD_FAILED", session_id=session_id)
        return []


def _safe_write_fact_to_memory(session_id: str, key: str, value: str, *, updated_at: float | None = None) -> None:
    if not _memory_writeback_enabled():
        return
    try:
        write_fact(session_id, key, value, updated_at=updated_at)
    except Exception:
        log_exception(logger, "MEMORY_FACT_WRITE_FAILED", session_id=session_id)


def _safe_write_turn_memories(
    *,
    session_id: str,
    request_query: str,
    final_answer: str,
    selected_skill: str | None,
    retrieved_doc_ids: list[str],
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
        log_exception(logger, "MEMORY_TURN_WRITEBACK_FAILED", session_id=session_id)


def _normalize_session_facts(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        fact_key = str(key or "").strip()
        fact_value = str(value or "").strip()
        if fact_key and fact_value:
            normalized[fact_key] = fact_value
    return normalized


def _normalize_session_messages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, str]] = []
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


def _normalize_session_state(_session_id: str, raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    return {
        "facts": _normalize_session_facts(state.get("facts", {})),
        "messages": _normalize_session_messages(state.get("messages", [])),
        "updated_at": _normalize_updated_at(state.get("updated_at")),
    }


def _normalize_candidate_name(raw: str) -> str | None:
    candidate = (raw or "").strip()
    candidate = candidate.strip(" \t\r\n,.!?;:，。！？；：" "\"\'()[]（）【】")
    if not candidate:
        return None

    lowered = candidate.lower()
    if candidate in INVALID_NAME_VALUES or lowered in INVALID_NAME_VALUES:
        return None
    if candidate.endswith(("吗", "呢", "么", "嘛")):
        return None
    return candidate


def session_rows() -> SessionRows:
    return open_session_rows(
        Path(agent_settings.SESSION_STATE_FILE),
        agent_settings.SESSION_STATE_DB,
        "agent_session_state",
        _normalize_session_state,
    )


def peek_session_state(session_id: str) -> dict[str, Any]:
    """Read an existing snapshot without creating or touching a session."""
    state = session_rows().read(session_id)
    if state is not None:
        state["messages"] = state["messages"][-SESSION_MAX_MESSAGES:]
    return state or {}


def _get_or_create_session_state(session_id: str) -> dict[str, Any]:
    sid = (session_id or "").strip()
    existing = peek_session_state(sid)
    if existing:
        return existing
    with session_rows().edit(sid) as state:
        return state


def _merge_session_facts(session_id: str, facts: dict[str, str]) -> dict[str, Any]:
    updates: dict[str, str] = {}
    for key, value in (facts or {}).items():
        fact_key = (key or "").strip()
        fact_value = (value or "").strip()
        if fact_key == "user_name":
            fact_value = _normalize_candidate_name(fact_value) or ""
        if fact_key and fact_value:
            updates[fact_key] = fact_value

    with session_rows().edit(session_id) as state:
        state["facts"].update(updates)
        state["messages"] = state["messages"][-SESSION_MAX_MESSAGES:]
        state["updated_at"] = time.time()

    for key, value in updates.items():
        _safe_write_fact_to_memory(session_id, key, value, updated_at=state["updated_at"])
    return state


def _append_session_messages(session_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    updates = _normalize_session_messages(messages)
    if not updates:
        return _get_or_create_session_state(session_id)
    with session_rows().edit(session_id) as state:
        state["messages"] = (state["messages"] + updates)[-SESSION_MAX_MESSAGES:]
        state["updated_at"] = time.time()
    return state


def _append_session_message_once(session_id: str, role: str, content: str) -> dict[str, Any]:
    updates = _normalize_session_messages([{"role": role, "content": content}])
    if not updates:
        return _get_or_create_session_state(session_id)

    with session_rows().edit(session_id) as state:
        candidate = updates[0]
        history = state["messages"][-SESSION_MAX_MESSAGES:]
        if not history or history[-1] != candidate:
            history = (history + updates)[-SESSION_MAX_MESSAGES:]
            state["updated_at"] = time.time()
        state["messages"] = history
    return state

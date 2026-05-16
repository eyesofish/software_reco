"""Pure helper functions for normalizing agent state into API response shapes.

Extracted from app/api/v1/routes.py to keep route handlers thin. All functions
here are pure (no I/O, no module-level mutable state) except
_iter_awaiting_confirmation_sse which yields SSE frames.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from ..session_store import _normalize_candidate_name
from ..stream_utils import _sse

# --- regex / constants -------------------------------------------------------

NAME_ZH_PATTERN = re.compile(
    r"(?:(?:^|[锛?銆傦紒锛?\s])(?:我叫|记住我叫|记住我的名字是|我的名字是)\s*([一-鿿A-Za-z][一-鿿A-Za-z0-9_\-]{0,31}))"
)
NAME_IS_ZH_PATTERN = re.compile(
    r"(?:(?:^|[锛?銆傦紒锛?\s])(?:我是)\s*([一-鿿A-Za-z][一-鿿A-Za-z0-9_\-]{0,31}))"
)
NAME_EN_PATTERN = re.compile(r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\-' ]{0,40})")
USER_NAME_QUESTION_PATTERNS = (
    re.compile(r"我叫(什么|啥|谁)"),
    re.compile(r"我的名字(是)?(什么|啥|谁)"),
    re.compile(r"我是谁"),
    re.compile(r"(?i)what is my name"),
    re.compile(r"(?i)who am i"),
)
CONFIRM_SHORT_QUERY_PATTERN = re.compile(
    r"(?i)^\s*(?:确认|继续|继续吧|好的|好|ok|okay|yes|y|go on|continue)\s*[.!?。！？]*\s*$"
)


# --- generic helpers ---------------------------------------------------------


def _get_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


# --- list/dict normalizers ---------------------------------------------------


def _normalize_candidates(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
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


def _normalize_retrieved_doc_ids(raw: Any) -> list[str]:
    normalized: list[str] = []
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


def _normalize_retrieval_records(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
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
        channel_counts: dict[str, int] = {}
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


def _normalize_plan_steps(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
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


def _normalize_hitl_payload(raw: Any) -> dict[str, Any] | None:
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


def _normalize_pending_sub_questions(raw: Any) -> list[str]:
    normalized: list[str] = []
    seen = set()

    def append_unique(value: str) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        normalized.append(text)

    def try_parse_structured_text(text: str) -> Any | None:
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
            preferred: list[Any] = []
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


# --- extractors --------------------------------------------------------------


def _extract_skill_planner_payload(result: Any) -> dict[str, Any]:
    selected_skill = str(_get_value(result, "selected_skill", "") or "").strip() or None
    plan_steps = _normalize_plan_steps(_get_value(result, "plan_steps", []))
    return {
        "selected_skill": selected_skill,
        "plan_steps": plan_steps,
    }


def _extract_eval_payload(
    result: Any,
    *,
    hitl_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


def _extract_interrupt_payload(result: Any) -> dict[str, Any] | None:
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


def _extract_name_fact(query: str) -> str | None:
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


# --- predicates --------------------------------------------------------------


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


# --- builders ----------------------------------------------------------------


def _build_human_confirmation_ack(sub_questions: list[str]) -> str:
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


# --- SSE streamers -----------------------------------------------------------


async def _iter_awaiting_confirmation_sse(
    session_id: str,
    sub_questions: list[str],
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

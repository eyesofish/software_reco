from __future__ import annotations

import re
import time
from typing import Any, Iterable

from .config import settings
from .memory_schema import MemoryItem
from .memory_store import get_facts, get_recent, semantic_search


def _tokenize(text: str) -> set[str]:
    normalized = str(text or "").lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return set(latin_tokens + cjk_chars)


def _normalize_tags(raw_tags: Iterable[Any]) -> list[str]:
    tags: list[str] = []
    seen = set()
    for item in raw_tags or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)
    return tags


def _normalize_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role in {"system", "user", "assistant"} and content:
            normalized.append({"role": role, "content": content})
    return normalized


def _working_items(session_id: str, messages: list[dict[str, str]], limit: int) -> list[MemoryItem]:
    if limit <= 0:
        return []
    now_ts = time.time()
    recent = messages[-limit:]
    output: list[MemoryItem] = []
    for index, item in enumerate(recent, start=1):
        role = item.get("role", "user")
        content = item.get("content", "").strip()
        if not content:
            continue
        recency_rank = index / max(len(recent), 1)
        salience = 0.45 if role == "user" else 0.35
        score = min(1.0, salience + (0.4 * recency_rank))
        output.append(
            MemoryItem(
                memory_id=f"working:{role}:{index}",
                session_id=session_id,
                level="working",
                content=f"[{role}] {content}",
                tags=[f"role:{role}", "working"],
                salience=salience,
                created_at=now_ts,
                updated_at=now_ts,
                score=round(score, 6),
            )
        )
    return output


def _fact_items(session_id: str, facts: dict[str, str], limit: int) -> list[MemoryItem]:
    if limit <= 0:
        return []
    now_ts = time.time()
    output: list[MemoryItem] = []
    for index, (key, value) in enumerate(facts.items(), start=1):
        fact_key = str(key or "").strip()
        fact_value = str(value or "").strip()
        if not fact_key or not fact_value:
            continue
        output.append(
            MemoryItem(
                memory_id=f"fact:{fact_key}",
                session_id=session_id,
                level="episodic",
                content=f"{fact_key}: {fact_value}",
                tags=[f"fact:{fact_key}", "fact", "episodic"],
                salience=0.9,
                created_at=now_ts,
                updated_at=now_ts,
                score=0.9,
            )
        )
        if len(output) >= limit:
            break
    return output


def _merge_facts(primary: dict[str, str], fallback: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (fallback, primary):
        for key, value in (source or {}).items():
            k = str(key or "").strip()
            v = str(value or "").strip()
            if k and v:
                merged[k] = v
    return merged


def _memory_item_to_context(item: MemoryItem) -> dict[str, Any]:
    return {
        "memory_id": item.memory_id,
        "session_id": item.session_id,
        "level": item.level,
        "content": item.content,
        "tags": _normalize_tags(item.tags),
        "salience": round(float(item.salience), 6),
        "score": round(float(item.score), 6),
        "updated_at": float(item.updated_at),
    }


def build_memory_context(
    *,
    session_id: str,
    query: str,
    messages: Any,
    facts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not bool(getattr(settings, "FEATURE_LAYERED_MEMORY", True)):
        return []

    sid = str(session_id or "").strip()
    if not sid:
        return []

    working_top_k = max(0, int(getattr(settings, "MEMORY_WORKING_TOP_K", 6)))
    episodic_top_k = max(0, int(getattr(settings, "MEMORY_EPISODIC_TOP_K", 6)))
    semantic_top_k = max(0, int(getattr(settings, "MEMORY_SEMANTIC_TOP_K", 4)))
    facts_top_k = max(0, int(getattr(settings, "MEMORY_FACT_TOP_K", 6)))
    context_limit = max(1, int(getattr(settings, "MEMORY_CONTEXT_MAX_ITEMS", 20)))

    normalized_messages = _normalize_messages(messages)
    merged_facts = _merge_facts(facts or {}, get_facts(sid))
    query_tokens = _tokenize(query)

    working_items = _working_items(sid, normalized_messages, working_top_k)
    fact_items = _fact_items(sid, merged_facts, facts_top_k)
    episodic_items = get_recent(sid, limit=episodic_top_k, levels=["episodic"])
    semantic_items = semantic_search(sid, query=query, top_k=semantic_top_k)

    combined: list[MemoryItem] = []
    combined.extend(fact_items)
    combined.extend(semantic_items)
    combined.extend(episodic_items)
    combined.extend(working_items)

    dedup: dict[str, MemoryItem] = {}
    for item in combined:
        if query_tokens:
            tokens = _tokenize(item.content) | _tokenize(" ".join(item.tags))
            overlap = len(query_tokens & tokens) / max(len(query_tokens), 1) if tokens else 0.0
        else:
            overlap = 0.0
        adaptive_score = max(item.score, (0.65 * overlap) + (0.35 * item.salience))
        item.score = round(min(1.0, max(0.0, adaptive_score)), 6)

        key = item.memory_id.strip() or f"{item.level}:{item.content.strip()}"
        current = dedup.get(key)
        if current is None or item.score > current.score:
            dedup[key] = item

    ranked = sorted(
        dedup.values(),
        key=lambda m: (float(m.score), float(m.salience), float(m.updated_at)),
        reverse=True,
    )
    return [_memory_item_to_context(item) for item in ranked[:context_limit]]

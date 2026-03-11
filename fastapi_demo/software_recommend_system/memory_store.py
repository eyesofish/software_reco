from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Sequence

from .config import settings
from .memory_schema import MemoryItem

logger = logging.getLogger(__name__)

_MEMORY_LOCK = RLock()
_MEMORY_STORE_LOADED = False
_MEMORY_STORE: dict[str, dict[str, Any]] = {}


def _get_store_path() -> Path:
    raw = str(getattr(settings, "MEMORY_STORE_FILE", ".runtime/layered_memory_store.json") or "").strip()
    if not raw:
        raw = ".runtime/layered_memory_store.json"
    return Path(raw)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tokenize(text: str) -> set[str]:
    normalized = str(text or "").lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return set(latin_tokens + cjk_chars)


def _normalize_tags(tags: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    seen = set()
    for item in tags or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _now() -> float:
    return time.time()


def _memory_id(session_id: str, level: str, content: str, now_ts: float) -> str:
    digest = hashlib.sha1(f"{session_id}|{level}|{content}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{level}:{int(now_ts * 1000)}:{digest}"


def _normalize_fact_value(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        value = str(raw.get("value", "") or "").strip()
        if not value:
            return None
        created_at = _safe_float(raw.get("created_at"), _now())
        updated_at = _safe_float(raw.get("updated_at"), created_at)
        return {
            "value": value,
            "created_at": created_at,
            "updated_at": updated_at,
        }
    value = str(raw or "").strip()
    if not value:
        return None
    now_ts = _now()
    return {"value": value, "created_at": now_ts, "updated_at": now_ts}


def _normalize_memory_item(raw: Any, *, session_id: str, level: str) -> MemoryItem | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content", "") or "").strip()
    if not content:
        return None
    created_at = _safe_float(raw.get("created_at"), _now())
    updated_at = _safe_float(raw.get("updated_at"), created_at)
    salience = max(0.0, min(1.0, _safe_float(raw.get("salience"), 0.0)))
    score = max(0.0, min(1.0, _safe_float(raw.get("score"), 0.0)))
    memory_id = str(raw.get("memory_id", "") or "").strip() or _memory_id(session_id, level, content, created_at)
    tags = _normalize_tags(raw.get("tags", []) or [])
    return MemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        level=level,  # type: ignore[arg-type]
        content=content,
        tags=tags,
        salience=salience,
        created_at=created_at,
        updated_at=updated_at,
        score=score,
    )


def _memory_item_dump(item: MemoryItem) -> dict[str, Any]:
    return item.model_dump()


def _normalize_session_payload(session_id: str, raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    normalized: dict[str, Any] = {
        "facts": {},
        "episodes": [],
        "semantics": [],
        "updated_at": _safe_float(payload.get("updated_at"), _now()),
    }

    facts = payload.get("facts", {})
    if isinstance(facts, dict):
        for key, value in facts.items():
            fact_key = str(key or "").strip()
            if not fact_key:
                continue
            normalized_value = _normalize_fact_value(value)
            if normalized_value is not None:
                normalized["facts"][fact_key] = normalized_value

    for level_key, bucket_key in (("episodic", "episodes"), ("semantic", "semantics")):
        raw_bucket = payload.get(bucket_key, [])
        if not isinstance(raw_bucket, list):
            continue
        parsed: list[MemoryItem] = []
        for entry in raw_bucket:
            normalized_item = _normalize_memory_item(entry, session_id=session_id, level=level_key)
            if normalized_item is not None:
                parsed.append(normalized_item)
        parsed.sort(key=lambda item: item.updated_at, reverse=True)
        normalized[bucket_key] = [_memory_item_dump(item) for item in parsed]

    return normalized


def _persist_locked() -> None:
    path = _get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_MEMORY_STORE, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_locked() -> None:
    global _MEMORY_STORE_LOADED, _MEMORY_STORE
    if _MEMORY_STORE_LOADED:
        return
    path = _get_store_path()
    if not path.exists():
        _MEMORY_STORE = {}
        _MEMORY_STORE_LOADED = True
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to load memory store from %s: %s", path, exc)
        _MEMORY_STORE = {}
        _MEMORY_STORE_LOADED = True
        return

    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for session_id, payload in raw.items():
            sid = str(session_id or "").strip()
            if not sid:
                continue
            normalized[sid] = _normalize_session_payload(sid, payload)

    _MEMORY_STORE = normalized
    _MEMORY_STORE_LOADED = True


def _ensure_loaded() -> None:
    with _MEMORY_LOCK:
        _load_locked()


def _ensure_session_locked(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    state = _MEMORY_STORE.get(sid)
    if state is None:
        state = {"facts": {}, "episodes": [], "semantics": [], "updated_at": _now()}
        _MEMORY_STORE[sid] = state
    return state


def _merge_tags(existing: Sequence[str], incoming: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in list(existing or []) + list(incoming or []):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _write_memory_item(
    *,
    session_id: str,
    level: str,
    content: str,
    tags: list[str] | None = None,
    salience: float = 0.0,
) -> MemoryItem | None:
    sid = str(session_id or "").strip()
    normalized_content = str(content or "").strip()
    if not sid or not normalized_content:
        return None
    level_key = str(level or "").strip().lower()
    if level_key == "working":
        return None
    if level_key not in {"episodic", "semantic"}:
        raise ValueError(f"unsupported memory level: {level}")

    normalized_salience = max(0.0, min(1.0, _safe_float(salience, 0.0)))
    min_semantic_salience = max(0.0, min(1.0, _safe_float(getattr(settings, "MEMORY_SEMANTIC_MIN_SALIENCE", 0.45), 0.45)))
    if level_key == "semantic" and normalized_salience < min_semantic_salience:
        return None

    now_ts = _now()
    normalized_tags = _normalize_tags(tags or [])
    max_items = max(20, int(getattr(settings, "MEMORY_MAX_ITEMS_PER_LEVEL", 200)))
    bucket_key = "episodes" if level_key == "episodic" else "semantics"

    with _MEMORY_LOCK:
        _load_locked()
        session_state = _ensure_session_locked(sid)
        bucket_raw = session_state.get(bucket_key, [])
        if not isinstance(bucket_raw, list):
            bucket_raw = []
            session_state[bucket_key] = bucket_raw

        for index, raw_item in enumerate(bucket_raw):
            existing_item = _normalize_memory_item(raw_item, session_id=sid, level=level_key)
            if existing_item is None:
                continue
            if existing_item.content != normalized_content:
                continue

            existing_item.updated_at = now_ts
            existing_item.salience = max(existing_item.salience, normalized_salience)
            existing_item.tags = _merge_tags(existing_item.tags, normalized_tags)
            bucket_raw[index] = _memory_item_dump(existing_item)
            session_state["updated_at"] = now_ts
            _persist_locked()
            return existing_item

        created = MemoryItem(
            memory_id=_memory_id(sid, level_key, normalized_content, now_ts),
            session_id=sid,
            level=level_key,  # type: ignore[arg-type]
            content=normalized_content,
            tags=normalized_tags,
            salience=normalized_salience,
            created_at=now_ts,
            updated_at=now_ts,
            score=0.0,
        )
        bucket_raw.append(_memory_item_dump(created))
        bucket_raw.sort(key=lambda item: _safe_float(item.get("updated_at"), 0.0), reverse=True)
        session_state[bucket_key] = bucket_raw[:max_items]
        session_state["updated_at"] = now_ts
        _persist_locked()
        return created


def write_fact(session_id: str, key: str, value: str) -> None:
    sid = str(session_id or "").strip()
    fact_key = str(key or "").strip()
    fact_value = str(value or "").strip()
    if not sid or not fact_key or not fact_value:
        return

    now_ts = _now()
    with _MEMORY_LOCK:
        _load_locked()
        session_state = _ensure_session_locked(sid)
        facts = session_state.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
            session_state["facts"] = facts

        existing = _normalize_fact_value(facts.get(fact_key))
        if existing is None:
            facts[fact_key] = {
                "value": fact_value,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        else:
            existing["value"] = fact_value
            existing["updated_at"] = now_ts
            facts[fact_key] = existing

        session_state["updated_at"] = now_ts
        _persist_locked()


def write_episode(session_id: str, content: str, tags: list[str] | None = None, salience: float = 0.4) -> MemoryItem | None:
    return _write_memory_item(
        session_id=session_id,
        level="episodic",
        content=content,
        tags=tags,
        salience=salience,
    )


def write_semantic(
    session_id: str,
    content: str,
    tags: list[str] | None = None,
    salience: float = 0.6,
) -> MemoryItem | None:
    return _write_memory_item(
        session_id=session_id,
        level="semantic",
        content=content,
        tags=tags,
        salience=salience,
    )


def get_facts(session_id: str) -> dict[str, str]:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    with _MEMORY_LOCK:
        _load_locked()
        session_state = _MEMORY_STORE.get(sid, {})
        raw_facts = session_state.get("facts", {})
        if not isinstance(raw_facts, dict):
            return {}
        facts: dict[str, str] = {}
        for key, raw_value in raw_facts.items():
            fact_key = str(key or "").strip()
            normalized = _normalize_fact_value(raw_value)
            if fact_key and normalized is not None:
                facts[fact_key] = normalized["value"]
        return facts


def get_recent(
    session_id: str,
    *,
    limit: int = 10,
    levels: Sequence[str] | None = None,
) -> list[MemoryItem]:
    sid = str(session_id or "").strip()
    if not sid:
        return []

    normalized_levels = {str(level or "").strip().lower() for level in (levels or ["episodic", "semantic"])}
    normalized_levels = {level for level in normalized_levels if level in {"episodic", "semantic"}}
    if not normalized_levels:
        return []

    with _MEMORY_LOCK:
        _load_locked()
        session_state = _MEMORY_STORE.get(sid, {})
        output: list[MemoryItem] = []
        if "episodic" in normalized_levels:
            for raw_item in session_state.get("episodes", []) or []:
                parsed = _normalize_memory_item(raw_item, session_id=sid, level="episodic")
                if parsed is not None:
                    output.append(parsed)
        if "semantic" in normalized_levels:
            for raw_item in session_state.get("semantics", []) or []:
                parsed = _normalize_memory_item(raw_item, session_id=sid, level="semantic")
                if parsed is not None:
                    output.append(parsed)

    output.sort(key=lambda item: item.updated_at, reverse=True)
    return output[: max(1, int(limit))]


def semantic_search(session_id: str, query: str, top_k: int = 5) -> list[MemoryItem]:
    sid = str(session_id or "").strip()
    if not sid:
        return []
    limit = max(1, int(top_k))
    query_tokens = _tokenize(query)

    with _MEMORY_LOCK:
        _load_locked()
        session_state = _MEMORY_STORE.get(sid, {})
        semantic_items: list[MemoryItem] = []
        for raw_item in session_state.get("semantics", []) or []:
            parsed = _normalize_memory_item(raw_item, session_id=sid, level="semantic")
            if parsed is not None:
                semantic_items.append(parsed)

    if not semantic_items:
        return []

    now_ts = _now()
    scored: list[tuple[float, MemoryItem]] = []
    for item in semantic_items:
        content_tokens = _tokenize(item.content)
        tag_tokens = _tokenize(" ".join(item.tags))
        memory_tokens = content_tokens | tag_tokens
        overlap = 0.0
        if query_tokens and memory_tokens:
            overlap = len(query_tokens & memory_tokens) / max(len(query_tokens), 1)

        age_days = max(0.0, (now_ts - item.updated_at) / 86400.0)
        recency = 1.0 / (1.0 + (age_days / 14.0))
        score = (0.55 * overlap) + (0.35 * item.salience) + (0.10 * recency)

        if query_tokens and overlap <= 0 and item.salience < 0.8:
            continue
        item.score = round(max(0.0, min(1.0, score)), 6)
        scored.append((item.score, item))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in scored[:limit]]


def clear_session(session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _MEMORY_LOCK:
        _load_locked()
        if sid in _MEMORY_STORE:
            _MEMORY_STORE.pop(sid, None)
            _persist_locked()


def reset_memory_store_for_tests() -> None:
    global _MEMORY_STORE_LOADED, _MEMORY_STORE
    with _MEMORY_LOCK:
        _MEMORY_STORE = {}
        _MEMORY_STORE_LOADED = False

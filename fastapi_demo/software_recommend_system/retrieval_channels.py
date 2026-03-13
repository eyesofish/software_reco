import logging
import re
import time
from typing import Any, Iterable, List

import chromadb
from rank_bm25 import BM25Okapi

from .config import settings
from .document_schema import Document
from .ingestion.indexer import get_chroma_collection
from .logging_utils import error_fields, log_event, new_trace_id, text_preview
from .tools import _tavily_search, similarity_search

logger = logging.getLogger(__name__)

_KEYWORD_COLLECTION_NAME = "software_recommendations"
_KEYWORD_RECALL_UNAVAILABLE_UNTIL = 0.0


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_tags(raw_tags: Any) -> list[str]:
    if isinstance(raw_tags, list):
        return [str(item).strip() for item in raw_tags if str(item).strip()]
    if isinstance(raw_tags, str):
        if not raw_tags.strip():
            return []
        return [item.strip() for item in re.split(r"[,;/|]", raw_tags) if item.strip()]
    return []


def _tokenize_terms(text: str) -> list[str]:
    normalized = str(text or "").lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return latin_tokens + cjk_chars


def _tokenize(text: str) -> set[str]:
    return set(_tokenize_terms(text))


def _keyword_circuit_ttl_seconds() -> float:
    try:
        ttl = float(getattr(settings, "KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS", 30))
    except Exception:
        ttl = 30.0
    return max(0.0, ttl)


def _collection_get_documents(scan_limit: int) -> dict[str, Any]:
    def _read(collection: Any) -> dict[str, Any]:
        try:
            return collection.get(include=["documents", "metadatas"], limit=scan_limit)
        except TypeError:
            return collection.get(include=["documents", "metadatas"])

    try:
        collection = get_chroma_collection(collection_name=_KEYWORD_COLLECTION_NAME)
        return _read(collection)
    except Exception as exc:
        # Chroma can intermittently throw tenant errors after collection rebuild.
        # Retry with a fresh client once before declaring the keyword channel unavailable.
        if "tenant" not in str(exc or "").lower():
            raise
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        collection = client.get_or_create_collection(_KEYWORD_COLLECTION_NAME)
        return _read(collection)


def recall_vector(query: str, top_k: int, trace_id: str | None = None) -> List[Document]:
    limit = max(1, int(top_k))
    return similarity_search(query=query, k=limit, trace_id=trace_id)[:limit]


def recall_web(query: str, top_k: int, trace_id: str | None = None) -> List[Document]:
    limit = max(1, int(top_k))
    return _tavily_search(query=query, trace_id=trace_id)[:limit]


def _keyword_corpus_tokens(text: str, metadata: dict[str, Any]) -> list[str]:
    tokens = _tokenize_terms(text)
    tags = _normalize_tags(metadata.get("tags"))
    if tags:
        tokens.extend(_tokenize_terms(" ".join(tags)))
    return tokens


def recall_keyword(query: str, top_k: int, trace_id: str | None = None) -> List[Document]:
    global _KEYWORD_RECALL_UNAVAILABLE_UNTIL
    limit = max(1, int(top_k))
    scan_limit = max(limit, int(getattr(settings, "RECALL_KEYWORD_SCAN_LIMIT", 2000)))
    query_tokens = _tokenize_terms(query)
    if not query_tokens:
        return []

    current_trace_id = trace_id or new_trace_id("search")
    query_hint = text_preview(query)
    if time.time() < _KEYWORD_RECALL_UNAVAILABLE_UNTIL:
        log_event(
            logger,
            logging.INFO,
            "search.keyword.skip",
            component="search",
            trace_id=current_trace_id,
            query=query_hint,
            top_k=limit,
            reason="circuit_open",
        )
        return []

    try:
        raw = _collection_get_documents(scan_limit)
        documents: Iterable[Any] = raw.get("documents") or []
        metadatas: Iterable[Any] = raw.get("metadatas") or []
        documents = list(documents)[:scan_limit]
        metadatas = list(metadatas)[:scan_limit]

        scored: list[tuple[float, str, dict[str, Any]]] = []
        corpus_rows: list[tuple[str, dict[str, Any], list[str], set[str]]] = []
        query_token_set = set(query_tokens)
        for idx, content in enumerate(documents):
            text = str(content or "").strip()
            if not text:
                continue
            metadata = {}
            if idx < len(metadatas):
                metadata = metadatas[idx] or {}

            text_tokens = _keyword_corpus_tokens(text, metadata)
            text_token_set = set(text_tokens)
            if not text_token_set:
                continue
            corpus_rows.append((text, metadata, text_tokens, text_token_set))

        if corpus_rows:
            bm25 = BM25Okapi([tokens for _, _, tokens, _ in corpus_rows])
            scores = bm25.get_scores(query_tokens)
            matched_rows: list[tuple[float, str, dict[str, Any]]] = []
            for score, (text, metadata, _, text_token_set) in zip(scores, corpus_rows):
                if not (query_token_set & text_token_set):
                    continue
                matched_rows.append((float(score), text, metadata))

            score_shift = 0.0
            if matched_rows and max(score for score, _, _ in matched_rows) <= 0:
                # BM25Okapi can return non-positive scores for very small corpora.
                # Shift only matching rows so real hits are retained without changing their order.
                score_shift = abs(min(score for score, _, _ in matched_rows)) + 1e-9

            for score, text, metadata in matched_rows:
                adjusted_score = score + score_shift
                if adjusted_score <= 0:
                    continue
                scored.append((adjusted_score, text, metadata))

        scored.sort(key=lambda item: item[0], reverse=True)
        top_items = scored[:limit]

        output: List[Document] = []
        for index, (score, content, metadata) in enumerate(top_items, start=1):
            output.append(
                Document(
                    content=content,
                    metadata={
                        "source": metadata.get("source", "keyword_index"),
                        "doc_id": (
                            metadata.get("source_doc_id")
                            or metadata.get("doc_id")
                            or metadata.get("filename")
                            or f"keyword:{index}"
                        ),
                        "author": metadata.get("author"),
                        "published_date": metadata.get("published_date"),
                        "updated_date": metadata.get("updated_date"),
                        "url": metadata.get("url"),
                        "tags": _normalize_tags(metadata.get("tags")),
                        "source_ranking": _safe_float(metadata.get("source_ranking"), 0.0),
                    },
                    score=float(score),
                )
            )

        log_event(
            logger,
            logging.INFO,
            "search.keyword.done",
            component="search",
            trace_id=current_trace_id,
            query=query_hint,
            docs=len(output),
            top_k=limit,
        )
        return output
    except Exception as exc:
        _KEYWORD_RECALL_UNAVAILABLE_UNTIL = time.time() + _keyword_circuit_ttl_seconds()
        log_event(
            logger,
            logging.WARNING,
            "search.keyword.fail",
            component="search",
            trace_id=current_trace_id,
            query=query_hint,
            top_k=limit,
            **error_fields(exc),
        )
        return []


def recall_memory(
    query: str,
    *,
    session_id: str | None,
    top_k: int,
    memory_context: list[dict[str, Any]] | None = None,
) -> List[Document]:
    limit = max(1, int(top_k))
    if not memory_context:
        return []

    query_tokens = _tokenize(query)
    current_session_id = str(session_id or "").strip()
    scored: list[tuple[float, Document]] = []
    level_boost = {
        "working": 0.9,
        "episodic": 0.75,
        "semantic": 0.8,
    }

    for index, item in enumerate(memory_context, start=1):
        content = str(
            _get_field(item, "content", "")
            or _get_field(item, "text", "")
            or _get_field(item, "summary", "")
        ).strip()
        if not content:
            continue
        memory_tags = _normalize_tags(_get_field(item, "tags", []))
        memory_level = str(_get_field(item, "level", "") or "episodic").strip().lower()
        source_session_id = str(_get_field(item, "session_id", "") or "").strip()
        memory_tokens = _tokenize(content)
        memory_tokens.update(_tokenize(" ".join(memory_tags)))

        overlap_score = 0.0
        if query_tokens and memory_tokens:
            overlap_score = len(query_tokens & memory_tokens) / max(len(query_tokens), 1)
        base_score = max(
            _safe_float(_get_field(item, "score", None), default=0.0),
            _safe_float(_get_field(item, "salience", None), default=0.0),
        )
        level_score = level_boost.get(memory_level, 0.7)
        session_boost = 0.08 if current_session_id and current_session_id == source_session_id else 0.0
        final_score = (0.45 * base_score) + (0.35 * overlap_score) + (0.20 * level_score) + session_boost
        final_score = max(0.0, min(1.0, final_score))

        doc = Document(
            content=content,
            metadata={
                "source": "memory",
                "doc_id": str(_get_field(item, "memory_id", "") or f"memory:{index}"),
                "url": None,
                "tags": memory_tags,
                "source_ranking": 1.0,
                "memory_level": memory_level,
                "session_id": source_session_id or current_session_id or None,
            },
            score=final_score,
        )
        scored.append((final_score, doc))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in scored[:limit]]

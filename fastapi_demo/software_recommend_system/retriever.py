import hashlib
import logging
import re
from typing import Any, Iterable, List

from .config import settings
from .document_schema import Document
from .logging_utils import log_event, new_trace_id, text_preview
from .observability import traceable
from .tools import _tavily_search, similarity_search

logger = logging.getLogger(__name__)

# Simple linear rerank weights (baseline, replaceable by CE/LLM reranker later).
_RETRIEVAL_SCORE_WEIGHT = 0.6
_KEYWORD_OVERLAP_WEIGHT = 0.3
_SOURCE_WEIGHT = 0.1
_SOURCE_PRIOR = {
    "vector": 1.0,
    "web": 0.8,
    "tavily": 0.8,
}


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_doc_id(metadata: Any, content: str, source: str, index: int) -> str:
    # Prefer upstream ids when available (vector metadata or web url).
    candidate = (
        _get_field(metadata, "doc_id")
        or _get_field(metadata, "source_doc_id")
        or _get_field(metadata, "url")
    )
    if candidate:
        return str(candidate)

    # Stable fallback id keeps downstream evaluation fields non-empty.
    digest = hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{source}:{digest}:{index}"


def _normalize_documents(documents: Iterable[Document], source_hint: str) -> List[Document]:
    normalized: List[Document] = []
    for index, doc in enumerate(documents):
        content = str(_get_field(doc, "content", "") or "")
        metadata = _get_field(doc, "metadata", {}) or {}
        source = str(_get_field(metadata, "source", source_hint) or source_hint)

        raw_score = _get_field(doc, "score", 0.0)
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0

        doc_id = _resolve_doc_id(metadata=metadata, content=content, source=source, index=index)
        normalized.append(
            Document(
                content=content,
                metadata={
                    "doc_id": doc_id,
                    "source": source,
                    "retrieval_source": source_hint,
                    "score": score,
                    "author": _get_field(metadata, "author"),
                    "published_date": _get_field(metadata, "published_date"),
                    "updated_date": _get_field(metadata, "updated_date"),
                    "url": _get_field(metadata, "url"),
                    "tags": _get_field(metadata, "tags", []) or [],
                    "source_ranking": _get_field(metadata, "source_ranking", 0.0) or 0.0,
                },
                score=score,
            )
        )
    return normalized


def _tokenize(text: str) -> set[str]:
    normalized = str(text or "").lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return set(latin_tokens + cjk_chars)


def _score_from_retrieval(doc: Document) -> float:
    metadata = _get_field(doc, "metadata", {}) or {}
    retrieval_source = str(_get_field(metadata, "retrieval_source", "") or "").lower()
    raw_score = _safe_float(_get_field(doc, "score", 0.0), default=0.0)

    if retrieval_source == "vector":
        distance = abs(raw_score)
        return 1.0 / (1.0 + distance)

    if raw_score <= 0:
        return 0.0
    if raw_score <= 1:
        return raw_score
    return raw_score / (1.0 + raw_score)


def _score_from_keyword_overlap(query_tokens: set[str], doc: Document) -> float:
    if not query_tokens:
        return 0.0

    doc_content = str(_get_field(doc, "content", "") or "")
    metadata = _get_field(doc, "metadata", {}) or {}
    doc_tokens = _tokenize(doc_content)
    doc_tokens.update(_tokenize(str(_get_field(metadata, "tags", "") or "")))
    if not doc_tokens:
        return 0.0

    overlap = len(query_tokens & doc_tokens)
    return overlap / max(len(query_tokens), 1)


def _score_from_source_prior(doc: Document) -> float:
    metadata = _get_field(doc, "metadata", {}) or {}
    retrieval_source = str(_get_field(metadata, "retrieval_source", "") or "").lower()
    source = str(_get_field(metadata, "source", "") or "").lower()
    source_ranking = _safe_float(_get_field(metadata, "source_ranking", 0.0), default=0.0)
    source_ranking = min(max(source_ranking, 0.0), 1.0)

    prior = _SOURCE_PRIOR.get(retrieval_source)
    if prior is None:
        prior = _SOURCE_PRIOR.get(source, 0.5)
    return max(prior, source_ranking)


def _apply_rerank_score(doc: Document, score: float) -> None:
    normalized_score = round(float(score), 6)
    doc.score = normalized_score
    metadata = _get_field(doc, "metadata", None)
    if isinstance(metadata, dict):
        metadata["score"] = normalized_score
        return

    if metadata is not None:
        try:
            setattr(metadata, "score", normalized_score)
        except Exception:
            pass


@traceable(name="rerank_documents")
def rerank_documents(query: str, docs: List[Document], top_n: int) -> List[Document]:
    """Rerank merged documents and keep top_n with normalized metadata.score."""

    if not docs:
        return []

    limit = max(1, int(top_n))
    query_tokens = _tokenize(query)
    scored_docs: List[Document] = []

    for doc in docs:
        retrieval_score = _score_from_retrieval(doc)
        overlap_score = _score_from_keyword_overlap(query_tokens, doc)
        source_score = _score_from_source_prior(doc)

        rerank_score = (
            (_RETRIEVAL_SCORE_WEIGHT * retrieval_score)
            + (_KEYWORD_OVERLAP_WEIGHT * overlap_score)
            + (_SOURCE_WEIGHT * source_score)
        )
        _apply_rerank_score(doc, rerank_score)
        scored_docs.append(doc)

    ranked_docs = sorted(
        scored_docs,
        key=lambda item: _safe_float(_get_field(item, "score", 0.0), default=0.0),
        reverse=True,
    )
    return ranked_docs[:limit]


@traceable(name="retrieve_shared")
def retrieve(query: str, top_k: int = 5) -> List[Document]:
    """Run shared hybrid retrieval (vector + web) and return normalized documents."""

    trace_id = new_trace_id("search")
    vector_docs = similarity_search(query=query, k=top_k)
    web_docs = _tavily_search(query=query)

    merged_docs: List[Document] = []
    merged_docs.extend(_normalize_documents(vector_docs, source_hint="vector"))
    merged_docs.extend(_normalize_documents(web_docs, source_hint="web"))

    merged_top5 = []
    for doc in merged_docs[:5]:
        metadata = _get_field(doc, "metadata", {}) or {}
        merged_top5.append(
            {
                "doc_id": str(_get_field(metadata, "doc_id", "") or ""),
                "score": round(_safe_float(_get_field(metadata, "score", _get_field(doc, "score", 0.0))), 6),
            }
        )

    if not settings.RETRIEVAL_ENABLE_RERANK:
        log_event(
            logger,
            logging.INFO,
            "search.retrieve.rerank.skip",
            component="search",
            trace_id=trace_id,
            query=text_preview(query),
            before_count=len(merged_docs),
            after_count=len(merged_docs),
            top5=merged_top5,
        )
        return merged_docs

    reranked_docs = rerank_documents(query=query, docs=merged_docs, top_n=top_k)

    top5 = []
    for doc in reranked_docs[:5]:
        metadata = _get_field(doc, "metadata", {}) or {}
        top5.append(
            {
                "doc_id": str(_get_field(metadata, "doc_id", "") or ""),
                "score": round(_safe_float(_get_field(metadata, "score", _get_field(doc, "score", 0.0))), 6),
            }
        )

    log_event(
        logger,
        logging.INFO,
        "search.retrieve.rerank.done",
        component="search",
        trace_id=trace_id,
        query=text_preview(query),
        before_count=len(merged_docs),
        after_count=len(reranked_docs),
        top5=top5,
    )
    return reranked_docs


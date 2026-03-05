import hashlib
from typing import Any, Iterable, List

from .document_schema import Document
from .observability import traceable
from .tools import _tavily_search, similarity_search


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


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


@traceable(name="retrieve_shared")
def retrieve(query: str, top_k: int = 5) -> List[Document]:
    """Run shared hybrid retrieval (vector + web) and return normalized documents."""

    vector_docs = similarity_search(query=query, k=top_k)
    web_docs = _tavily_search(query=query)

    merged_docs: List[Document] = []
    merged_docs.extend(_normalize_documents(vector_docs, source_hint="vector"))
    merged_docs.extend(_normalize_documents(web_docs, source_hint="web"))
    return merged_docs

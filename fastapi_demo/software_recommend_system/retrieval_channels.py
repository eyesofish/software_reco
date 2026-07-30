import logging
import math
import re
import time
from collections.abc import Iterable
from typing import Any

import chromadb
from rank_bm25 import BM25Okapi

from .config import settings
from .document_schema import Document, Metadata
from .image_embedder import ImageEmbeddingError, embed_image_texts
from .ingestion.indexer import get_chroma_collection, get_image_collection
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id, text_preview
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


def recall_vector(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
    limit = max(1, int(top_k))
    return similarity_search(query=query, k=limit, trace_id=trace_id)[:limit]


def _query_result_rows(raw: Any) -> list[list[Any]]:
    if not isinstance(raw, list):
        return []
    if raw and not isinstance(raw[0], list):
        return [list(raw)]
    return [list(row or []) for row in raw]


def _validated_query_embeddings(
    query_image_embeddings: list[list[float]] | None,
) -> list[list[float]]:
    vectors: list[list[float]] = []
    expected_dimension = 0
    for raw_vector in query_image_embeddings or []:
        try:
            vector = [float(value) for value in list(raw_vector or [])]
        except (TypeError, ValueError):
            continue
        if not vector or not all(math.isfinite(value) for value in vector):
            continue
        if expected_dimension and len(vector) != expected_dimension:
            continue
        expected_dimension = len(vector)
        vectors.append(vector)
    return vectors


def _document_metadata_dict(document: Document) -> dict[str, Any]:
    metadata = _get_field(document, "metadata", {}) or {}
    if isinstance(metadata, dict):
        return dict(metadata)
    model_dump = getattr(metadata, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def recall_image_vector(
    query: str,
    top_k: int,
    query_image_embeddings: list[list[float]] | None = None,
    seed_documents: list[Document] | None = None,
    trace_id: str | None = None,
) -> list[Document]:
    """Retrieve knowledge images from text and image queries in one shared space."""
    limit = max(1, int(top_k))
    current_trace_id = trace_id or new_trace_id("search")
    query_hint = text_preview(query)
    seed_docs = list(seed_documents or [])
    query_vectors: list[list[float]] = []
    query_modalities: list[str] = []

    normalized_query = str(query or "").strip()
    if normalized_query:
        try:
            text_vectors = embed_image_texts([normalized_query])
            if text_vectors:
                query_vectors.append(text_vectors[0])
                query_modalities.append("text")
        except ImageEmbeddingError as exc:
            log_event(
                logger,
                logging.WARNING,
                "search.image_vector.text_embedding.fail",
                component="search",
                trace_id=current_trace_id,
                query=query_hint,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    image_vectors = _validated_query_embeddings(query_image_embeddings)
    if query_vectors:
        expected_dimension = len(query_vectors[0])
        image_vectors = [
            vector for vector in image_vectors if len(vector) == expected_dimension
        ]
    query_vectors.extend(image_vectors)
    query_modalities.extend(["image"] * len(image_vectors))
    if not query_vectors and not seed_docs:
        return []

    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "search.image_vector.start",
        component="search",
        trace_id=current_trace_id,
        query=query_hint,
        query_vectors=len(query_vectors),
        image_query_vectors=len(image_vectors),
        seed_documents=len(seed_docs),
        top_k=limit,
    )

    results: dict[str, Any] = {}
    if query_vectors:
        try:
            collection = get_image_collection()
            available = max(0, int(collection.count()))
            if available > 0:
                results = collection.query(
                    query_embeddings=query_vectors,
                    n_results=min(limit, available),
                    include=["documents", "metadatas", "distances"],
                )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "search.image_vector.query.fail",
                component="search",
                trace_id=current_trace_id,
                query=query_hint,
                degraded_to_seed_documents=bool(seed_docs),
                elapsed_ms=elapsed_ms(started_at),
                **error_fields(exc),
            )

    id_rows = _query_result_rows((results or {}).get("ids"))
    document_rows = _query_result_rows((results or {}).get("documents"))
    metadata_rows = _query_result_rows((results or {}).get("metadatas"))
    distance_rows = _query_result_rows((results or {}).get("distances"))
    rrf_k = max(1, int(getattr(settings, "MULTIMODAL_RRF_K", 60)))
    fused: dict[str, dict[str, Any]] = {}

    def add_ranked_result(
        *,
        doc_id: str,
        content: str,
        metadata: dict[str, Any],
        distance: float,
        modalities: set[str],
        rank: int,
        replace_content: bool = True,
    ) -> None:
        entry = fused.setdefault(
            doc_id,
            {
                "rrf_score": 0.0,
                "content": content,
                "metadata": dict(metadata),
                "distance": distance,
                "modalities": set(),
                "best_rank": rank,
            },
        )
        entry["rrf_score"] += 1.0 / (rrf_k + rank)
        entry["modalities"].update(modalities)
        entry["best_rank"] = min(int(entry["best_rank"]), rank)
        for key, value in metadata.items():
            if value not in (None, "", []) and not entry["metadata"].get(key):
                entry["metadata"][key] = value
        if replace_content and len(content) > len(str(entry["content"] or "")):
            entry["content"] = content
        if distance < float(entry["distance"]):
            entry["distance"] = distance

    ranked_list_count = 0
    for query_index, ids in enumerate(id_rows):
        ranked_list_count += 1
        modality = (
            query_modalities[query_index]
            if query_index < len(query_modalities)
            else "unknown"
        )
        documents = document_rows[query_index] if query_index < len(document_rows) else []
        metadatas = metadata_rows[query_index] if query_index < len(metadata_rows) else []
        distances = distance_rows[query_index] if query_index < len(distance_rows) else []
        for rank, raw_id in enumerate(ids, start=1):
            metadata = metadatas[rank - 1] if rank - 1 < len(metadatas) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            doc_id = str(
                metadata.get("source_doc_id")
                or metadata.get("doc_id")
                or raw_id
                or ""
            ).strip()
            if not doc_id:
                continue
            content = (
                str(documents[rank - 1] or "").strip()
                if rank - 1 < len(documents)
                else ""
            )
            try:
                distance = float(distances[rank - 1])
            except (IndexError, TypeError, ValueError):
                distance = float("inf")
            add_ranked_result(
                doc_id=doc_id,
                content=content,
                metadata=metadata,
                distance=distance,
                modalities={modality},
                rank=rank,
            )

    if seed_docs:
        ranked_list_count += 1
        for rank, seed_doc in enumerate(seed_docs[:limit], start=1):
            metadata = _document_metadata_dict(seed_doc)
            doc_id = str(
                metadata.get("doc_id")
                or metadata.get("source_doc_id")
                or ""
            ).strip()
            if not doc_id:
                continue
            raw_distance = metadata.get("vector_distance")
            try:
                distance = (
                    float(raw_distance)
                    if isinstance(raw_distance, (int, float, str))
                    else float("inf")
                )
            except (TypeError, ValueError):
                distance = float("inf")
            modalities = {
                str(item).strip()
                for item in list(metadata.get("query_modalities") or ["image"])
                if str(item).strip()
            }
            add_ranked_result(
                doc_id=doc_id,
                content=str(_get_field(seed_doc, "content", "") or ""),
                metadata=metadata,
                distance=distance,
                modalities=modalities or {"image"},
                rank=rank,
                replace_content=False,
            )

    max_rrf_score = ranked_list_count / (rrf_k + 1)
    output_documents: list[Document] = []
    for doc_id, entry in fused.items():
        metadata = entry["metadata"] if isinstance(entry["metadata"], dict) else {}
        score = (
            min(1.0, float(entry["rrf_score"]) / max_rrf_score)
            if max_rrf_score > 0
            else 0.0
        )
        output_documents.append(
            Document(
                content=str(entry["content"] or metadata.get("filename") or doc_id),
                metadata=Metadata(
                    source=str(metadata.get("source", "image_vector")),
                    doc_id=doc_id,
                    retrieval_source="image_vector",
                    channel="image_vector",
                    channel_score=score,
                    score=score,
                    filename=(
                        str(metadata["filename"])
                        if metadata.get("filename")
                        else None
                    ),
                    media_type=(
                        str(metadata["media_type"])
                        if metadata.get("media_type")
                        else None
                    ),
                    modality=str(metadata.get("modality", "image")),
                    asset_path=(
                        str(metadata["asset_path"])
                        if metadata.get("asset_path")
                        else None
                    ),
                    asset_url=(
                        str(metadata["asset_url"])
                        if metadata.get("asset_url")
                        else None
                    ),
                    tags=_normalize_tags(metadata.get("tags")),
                    source_ranking=_safe_float(
                        metadata.get("source_ranking"),
                        0.0,
                    ),
                    matched_channels=["image_vector"],
                    query_modalities=sorted(entry["modalities"]),
                    channel_rank=int(entry["best_rank"]),
                    fusion_score=score,
                    vector_distance=(
                        float(entry["distance"])
                        if math.isfinite(float(entry["distance"]))
                        else None
                    ),
                ),
                score=score,
            )
        )

    ranked = sorted(
        output_documents,
        key=lambda item: item.score,
        reverse=True,
    )[:limit]
    log_event(
        logger,
        logging.INFO,
        "search.image_vector.done",
        component="search",
        trace_id=current_trace_id,
        query=query_hint,
        query_vectors=len(query_vectors),
        seed_documents=len(seed_docs),
        docs=len(ranked),
        elapsed_ms=elapsed_ms(started_at),
    )
    return ranked


def recall_web(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
    limit = max(1, int(top_k))
    return _tavily_search(query=query, trace_id=trace_id)[:limit]


def _keyword_corpus_tokens(text: str, metadata: dict[str, Any]) -> list[str]:
    tokens = _tokenize_terms(text)
    tags = _normalize_tags(metadata.get("tags"))
    if tags:
        tokens.extend(_tokenize_terms(" ".join(tags)))
    return tokens


def recall_keyword(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
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
            for score, (text, metadata, _, text_token_set) in zip(scores, corpus_rows, strict=True):
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

        output: list[Document] = []
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
) -> list[Document]:
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

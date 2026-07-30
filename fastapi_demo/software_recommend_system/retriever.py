import hashlib
import logging
import math
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from .config import settings
from .document_schema import Document
from .logging_utils import log_event, new_trace_id, text_preview
from .observability import traceable
from .retrieval_channels import (
    recall_image_vector,
    recall_keyword,
    recall_memory,
    recall_vector,
    recall_web,
)

logger = logging.getLogger(__name__)

# Simple linear rerank weights (baseline, override via env in settings).
_RETRIEVAL_SCORE_WEIGHT = 0.35
_KEYWORD_OVERLAP_WEIGHT = 0.25
_SOURCE_WEIGHT = 0.1
_FRESHNESS_WEIGHT = 0.1
_SKILL_WEIGHT = 0.1
_MEMORY_WEIGHT = 0.1
_SOURCE_PRIOR = {
    "vector": 1.0,
    "image_vector": 1.0,
    "multimodal_vector": 1.0,
    "web": 0.8,
    "tavily": 0.8,
    "keyword": 0.7,
    "memory": 0.9,
}

_CROSS_ENCODER_MODEL: Any = None
_CROSS_ENCODER_INIT_FAILED = False
_CROSS_ENCODER_INIT_LOCK = Lock()


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_weights() -> dict[str, float]:
    raw = {
        "retrieval": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_RETRIEVAL", _RETRIEVAL_SCORE_WEIGHT),
            _RETRIEVAL_SCORE_WEIGHT,
        ),
        "overlap": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_OVERLAP", _KEYWORD_OVERLAP_WEIGHT),
            _KEYWORD_OVERLAP_WEIGHT,
        ),
        "source": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_SOURCE", _SOURCE_WEIGHT),
            _SOURCE_WEIGHT,
        ),
        "freshness": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_FRESHNESS", _FRESHNESS_WEIGHT),
            _FRESHNESS_WEIGHT,
        ),
        "skill": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_SKILL", _SKILL_WEIGHT),
            _SKILL_WEIGHT,
        ),
        "memory": _safe_float(
            getattr(settings, "RERANK_LINEAR_WEIGHT_MEMORY", _MEMORY_WEIGHT),
            _MEMORY_WEIGHT,
        ),
    }
    clipped = {key: max(0.0, value) for key, value in raw.items()}
    total = sum(clipped.values())
    if total <= 0:
        return {
            "retrieval": _RETRIEVAL_SCORE_WEIGHT,
            "overlap": _KEYWORD_OVERLAP_WEIGHT,
            "source": _SOURCE_WEIGHT,
            "freshness": _FRESHNESS_WEIGHT,
            "skill": _SKILL_WEIGHT,
            "memory": _MEMORY_WEIGHT,
        }
    return {key: value / total for key, value in clipped.items()}


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _normalize_model_score(raw_score: Any) -> float:
    score = _safe_float(raw_score, default=0.0)
    if 0.0 <= score <= 1.0:
        return score
    return _sigmoid(score)


def _load_cross_encoder() -> Any:
    global _CROSS_ENCODER_MODEL, _CROSS_ENCODER_INIT_FAILED

    if not settings.RERANK_MODEL_ENABLED:
        return None
    if _CROSS_ENCODER_MODEL is not None:
        return _CROSS_ENCODER_MODEL
    if _CROSS_ENCODER_INIT_FAILED:
        return None

    with _CROSS_ENCODER_INIT_LOCK:
        if _CROSS_ENCODER_MODEL is not None:
            return _CROSS_ENCODER_MODEL
        if _CROSS_ENCODER_INIT_FAILED:
            return None

        try:
            from sentence_transformers import CrossEncoder

            _CROSS_ENCODER_MODEL = CrossEncoder(
                model_name_or_path=settings.RERANK_MODEL_NAME,
                device=settings.RERANK_MODEL_DEVICE,
                max_length=settings.RERANK_MODEL_MAX_LENGTH,
                local_files_only=settings.RERANK_MODEL_LOCAL_FILES_ONLY,
            )
            log_event(
                logger,
                logging.INFO,
                "search.rerank.model.ready",
                component="search",
                model=settings.RERANK_MODEL_NAME,
                device=settings.RERANK_MODEL_DEVICE,
                local_files_only=settings.RERANK_MODEL_LOCAL_FILES_ONLY,
            )
        except Exception as exc:
            _CROSS_ENCODER_INIT_FAILED = True
            log_event(
                logger,
                logging.WARNING,
                "search.rerank.model.fail",
                component="search",
                model=settings.RERANK_MODEL_NAME,
                device=settings.RERANK_MODEL_DEVICE,
                local_files_only=settings.RERANK_MODEL_LOCAL_FILES_ONLY,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None
    return _CROSS_ENCODER_MODEL


def _score_with_cross_encoder(query: str, docs: list[Document]) -> list[float] | None:
    model = _load_cross_encoder()
    if model is None or not docs:
        return None

    query_text = str(query or "").strip()
    if not query_text:
        return None

    pairs = [(query_text, str(_get_field(doc, "content", "") or "")) for doc in docs]
    try:
        raw_scores = model.predict(
            pairs,
            batch_size=settings.RERANK_MODEL_BATCH_SIZE,
            show_progress_bar=False,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "search.rerank.model.predict.fail",
            component="search",
            model=settings.RERANK_MODEL_NAME,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    return [_normalize_model_score(item) for item in raw_scores]


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


def _normalize_documents(documents: Iterable[Document], source_hint: str, channel: str) -> list[Document]:
    normalized: list[Document] = []
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
                    "channel": channel,
                    "channel_score": score,
                    "score": score,
                    "author": _get_field(metadata, "author"),
                    "published_date": _get_field(metadata, "published_date"),
                    "updated_date": _get_field(metadata, "updated_date"),
                    "url": _get_field(metadata, "url"),
                    "filename": _get_field(metadata, "filename"),
                    "media_type": _get_field(metadata, "media_type"),
                    "modality": _get_field(metadata, "modality"),
                    "asset_path": _get_field(metadata, "asset_path"),
                    "asset_url": _get_field(metadata, "asset_url"),
                    "tags": _get_field(metadata, "tags", []) or [],
                    "skill_tags": _get_field(metadata, "skill_tags", []) or [],
                    "memory_level": _get_field(metadata, "memory_level"),
                    "session_id": _get_field(metadata, "session_id"),
                    "source_ranking": _get_field(metadata, "source_ranking", 0.0) or 0.0,
                    "matched_channels": _get_field(metadata, "matched_channels", []) or [channel],
                    "query_modalities": _get_field(metadata, "query_modalities", []) or [],
                    "channel_rank": _get_field(metadata, "channel_rank"),
                    "fusion_score": _get_field(metadata, "fusion_score", 0.0) or 0.0,
                    "vector_distance": _get_field(metadata, "vector_distance"),
                    "rerank_features": _get_field(metadata, "rerank_features", {}) or {},
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
    if not retrieval_source:
        retrieval_source = str(_get_field(metadata, "channel", "") or "").lower()
    raw_score = _safe_float(_get_field(doc, "score", 0.0), default=0.0)

    if retrieval_source == "vector":
        distance = abs(raw_score)
        return 1.0 / (1.0 + distance)
    if retrieval_source in {"image_vector", "multimodal_vector"}:
        return min(max(raw_score, 0.0), 1.0)

    if retrieval_source == "keyword":
        return min(max(raw_score, 0.0), 1.0)
    if retrieval_source == "memory":
        return min(max(raw_score, 0.0), 1.0)

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
    if not retrieval_source:
        retrieval_source = str(_get_field(metadata, "channel", "") or "").lower()
    source = str(_get_field(metadata, "source", "") or "").lower()
    source_ranking = _safe_float(_get_field(metadata, "source_ranking", 0.0), default=0.0)
    source_ranking = min(max(source_ranking, 0.0), 1.0)

    prior = _SOURCE_PRIOR.get(retrieval_source)
    matched_channels = _get_field(metadata, "matched_channels", []) or []
    if isinstance(matched_channels, list) and matched_channels:
        matched_priors = [
            _SOURCE_PRIOR.get(str(channel or "").lower(), 0.0)
            for channel in matched_channels
        ]
        prior = max([prior or 0.0, *matched_priors])
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
        with suppress(Exception):
            metadata.score = normalized_score


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _score_from_freshness(doc: Document) -> float:
    metadata = _get_field(doc, "metadata", {}) or {}
    updated_date = _parse_datetime(_get_field(metadata, "updated_date"))
    published_date = _parse_datetime(_get_field(metadata, "published_date"))
    reference = updated_date or published_date
    if reference is None:
        return 0.0
    age_days = (datetime.now(UTC) - reference).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    # 30 days half-life style decay.
    return 1.0 / (1.0 + (age_days / 30.0))


def _score_from_skill_match(selected_skill: str | None, doc: Document) -> float:
    normalized_skill = str(selected_skill or "").strip().lower().replace("_", " ")
    if not normalized_skill:
        return 0.0
    skill_tokens = _tokenize(normalized_skill)
    if not skill_tokens:
        return 0.0

    metadata = _get_field(doc, "metadata", {}) or {}
    tags = _get_field(metadata, "tags", []) or []
    skill_tags = _get_field(metadata, "skill_tags", []) or []
    bag = " ".join(str(item) for item in list(tags) + list(skill_tags))
    doc_tokens = _tokenize(bag)
    doc_tokens.update(_tokenize(str(_get_field(doc, "content", "") or "")))
    if not doc_tokens:
        return 0.0
    overlap = len(skill_tokens & doc_tokens)
    return overlap / max(len(skill_tokens), 1)


def _score_from_memory_boost(doc: Document, session_id: str | None) -> float:
    metadata = _get_field(doc, "metadata", {}) or {}
    retrieval_source = str(_get_field(metadata, "retrieval_source", "") or "").lower()
    channel = str(_get_field(metadata, "channel", "") or "").lower()
    memory_level = str(_get_field(metadata, "memory_level", "") or "").strip().lower()
    doc_session_id = str(_get_field(metadata, "session_id", "") or "").strip()
    current_session_id = str(session_id or "").strip()

    if retrieval_source == "memory" or channel == "memory":
        return 1.0
    if current_session_id and current_session_id == doc_session_id:
        return 0.6
    if memory_level in {"working", "episodic", "semantic"}:
        return 0.4
    return 0.0


def _set_rerank_features(doc: Document, features: dict[str, float], mode: str) -> None:
    metadata = _get_field(doc, "metadata", None)
    payload = {k: round(_safe_float(v), 6) for k, v in features.items()}
    payload["mode"] = mode
    if isinstance(metadata, dict):
        metadata["rerank_features"] = payload
    elif metadata is not None:
        with suppress(Exception):
            metadata.rerank_features = payload
    doc.freshness_score = payload.get("freshness", 0.0)


def _dedup_key(doc: Document, index: int) -> str:
    metadata = _get_field(doc, "metadata", {}) or {}
    doc_id = str(_get_field(metadata, "doc_id", "") or "").strip()
    if doc_id:
        return f"doc_id:{doc_id}"
    url = str(_get_field(metadata, "url", "") or "").strip()
    if url:
        return f"url:{url}"
    source = str(_get_field(metadata, "source", "") or "").strip()
    content = str(_get_field(doc, "content", "") or "")
    digest = hashlib.sha1(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"fallback:{source}:{digest}:{index}"


def _metadata_richness(doc: Document) -> int:
    metadata = _get_field(doc, "metadata", {}) or {}
    fields = ("filename", "media_type", "modality", "asset_path", "asset_url")
    return sum(1 for field in fields if _get_field(metadata, field))


def _merge_document_metadata(target: Document, source: Document) -> None:
    target_metadata = _get_field(target, "metadata", None)
    source_metadata = _get_field(source, "metadata", None)
    if target_metadata is None or source_metadata is None:
        return

    scalar_fields = (
        "filename",
        "media_type",
        "modality",
        "asset_path",
        "asset_url",
        "url",
        "author",
        "published_date",
        "updated_date",
        "memory_level",
        "session_id",
        "vector_distance",
    )
    for field in scalar_fields:
        if not _get_field(target_metadata, field) and _get_field(source_metadata, field):
            with suppress(Exception):
                setattr(target_metadata, field, _get_field(source_metadata, field))

    for field in ("tags", "skill_tags", "matched_channels", "query_modalities"):
        target_values = list(_get_field(target_metadata, field, []) or [])
        source_values = list(_get_field(source_metadata, field, []) or [])
        merged_values = list(dict.fromkeys([*target_values, *source_values]))
        with suppress(Exception):
            setattr(target_metadata, field, merged_values)


def _fuse_local_vector_documents(
    vector_docs: list[Document],
    image_vector_docs: list[Document],
) -> list[Document]:
    if not image_vector_docs:
        return vector_docs
    if not vector_docs:
        return image_vector_docs

    rrf_k = max(1, int(getattr(settings, "MULTIMODAL_RRF_K", 60)))
    text_weight = max(
        0.0,
        _safe_float(getattr(settings, "MULTIMODAL_TEXT_VECTOR_WEIGHT", 1.0), 1.0),
    )
    image_weight = max(
        0.0,
        _safe_float(getattr(settings, "MULTIMODAL_IMAGE_VECTOR_WEIGHT", 1.0), 1.0),
    )
    if text_weight + image_weight <= 0:
        text_weight = image_weight = 1.0

    fused: dict[str, dict[str, Any]] = {}
    channel_specs = (
        ("vector", vector_docs, text_weight),
        ("image_vector", image_vector_docs, image_weight),
    )
    max_rrf_score = sum(weight / (rrf_k + 1) for _, docs, weight in channel_specs if docs)

    for channel, docs, weight in channel_specs:
        seen_channel_keys: set[str] = set()
        for rank, doc in enumerate(docs, start=1):
            key = _dedup_key(doc, index=rank)
            entry = fused.get(key)
            if key in seen_channel_keys:
                if entry is not None:
                    representative = entry["doc"]
                    if _metadata_richness(doc) > _metadata_richness(representative):
                        replacement = doc.model_copy(deep=True)
                        _merge_document_metadata(replacement, representative)
                        entry["doc"] = replacement
                    else:
                        _merge_document_metadata(representative, doc)
                continue
            seen_channel_keys.add(key)
            if entry is None:
                entry = {
                    "doc": doc.model_copy(deep=True),
                    "rrf_score": 0.0,
                    "channels": set(),
                    "best_rank": rank,
                }
                fused[key] = entry
            else:
                representative = entry["doc"]
                if (
                    _metadata_richness(doc) > _metadata_richness(representative)
                    or _score_from_retrieval(doc) > _score_from_retrieval(representative)
                ):
                    replacement = doc.model_copy(deep=True)
                    _merge_document_metadata(replacement, representative)
                    entry["doc"] = replacement
                else:
                    _merge_document_metadata(representative, doc)

            entry["rrf_score"] += weight / (rrf_k + rank)
            entry["channels"].add(channel)
            entry["best_rank"] = min(int(entry["best_rank"]), rank)

    output: list[Document] = []
    for entry in fused.values():
        doc = entry["doc"]
        score = (
            min(1.0, float(entry["rrf_score"]) / max_rrf_score)
            if max_rrf_score > 0
            else 0.0
        )
        doc.score = score
        metadata = doc.metadata
        metadata.retrieval_source = "multimodal_vector"
        metadata.channel = "multimodal_vector"
        metadata.channel_score = score
        metadata.score = score
        metadata.matched_channels = sorted(entry["channels"])
        metadata.channel_rank = int(entry["best_rank"])
        metadata.fusion_score = score
        output.append(doc)
    return sorted(output, key=lambda item: item.score, reverse=True)


def _deduplicate_documents(docs: list[Document]) -> list[Document]:
    best_by_key: dict[str, Document] = {}
    for index, doc in enumerate(docs, start=1):
        key = _dedup_key(doc, index=index)
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = doc
            continue
        incoming_score = _score_from_retrieval(doc)
        current_score = _score_from_retrieval(current)
        if incoming_score > current_score:
            _merge_document_metadata(doc, current)
            best_by_key[key] = doc
        else:
            _merge_document_metadata(current, doc)
    return list(best_by_key.values())


def _channel_counts(docs: list[Document]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in docs:
        metadata = _get_field(doc, "metadata", {}) or {}
        channel = str(_get_field(metadata, "channel", "") or _get_field(metadata, "retrieval_source", "") or "unknown")
        channel = channel.lower()
        counts[channel] = counts.get(channel, 0) + 1
    return counts


@traceable(name="rerank_documents")
def rerank_documents(
    query: str,
    docs: list[Document],
    top_n: int,
    *,
    selected_skill: str | None = None,
    session_id: str | None = None,
) -> list[Document]:
    """Rerank merged documents and keep top_n with normalized metadata.score."""

    if not docs:
        return []

    limit = max(1, int(top_n))
    scored_docs: list[Document] = list(docs)
    mode = "linear"
    weights = _normalize_weights()
    query_tokens = _tokenize(query)

    cross_encoder_scores = _score_with_cross_encoder(query, scored_docs)
    if cross_encoder_scores is not None and len(cross_encoder_scores) == len(scored_docs):
        mode = "cross_encoder_hybrid"
        for doc, ce_score in zip(scored_docs, cross_encoder_scores, strict=True):
            retrieval_score = _score_from_retrieval(doc)
            overlap_score = _score_from_keyword_overlap(query_tokens, doc)
            source_score = _score_from_source_prior(doc)
            freshness_score = _score_from_freshness(doc)
            skill_score = _score_from_skill_match(selected_skill, doc)
            memory_score = _score_from_memory_boost(doc, session_id)

            linear_score = (
                (weights["retrieval"] * retrieval_score)
                + (weights["overlap"] * overlap_score)
                + (weights["source"] * source_score)
                + (weights["freshness"] * freshness_score)
                + (weights["skill"] * skill_score)
                + (weights["memory"] * memory_score)
            )
            final_score = (0.8 * ce_score) + (0.2 * linear_score)
            _apply_rerank_score(doc, final_score)
            _set_rerank_features(
                doc,
                {
                    "cross_encoder": ce_score,
                    "retrieval": retrieval_score,
                    "overlap": overlap_score,
                    "source": source_score,
                    "freshness": freshness_score,
                    "skill": skill_score,
                    "memory": memory_score,
                    "linear": linear_score,
                    "final": final_score,
                },
                mode=mode,
            )
    else:
        for doc in scored_docs:
            retrieval_score = _score_from_retrieval(doc)
            overlap_score = _score_from_keyword_overlap(query_tokens, doc)
            source_score = _score_from_source_prior(doc)
            freshness_score = _score_from_freshness(doc)
            skill_score = _score_from_skill_match(selected_skill, doc)
            memory_score = _score_from_memory_boost(doc, session_id)

            rerank_score = (
                (weights["retrieval"] * retrieval_score)
                + (weights["overlap"] * overlap_score)
                + (weights["source"] * source_score)
                + (weights["freshness"] * freshness_score)
                + (weights["skill"] * skill_score)
                + (weights["memory"] * memory_score)
            )
            _apply_rerank_score(doc, rerank_score)
            _set_rerank_features(
                doc,
                {
                    "retrieval": retrieval_score,
                    "overlap": overlap_score,
                    "source": source_score,
                    "freshness": freshness_score,
                    "skill": skill_score,
                    "memory": memory_score,
                    "final": rerank_score,
                },
                mode=mode,
            )

    log_event(
        logger,
        logging.INFO,
        "search.rerank.rank.done",
        component="search",
        mode=mode,
        model=settings.RERANK_MODEL_NAME if mode.startswith("cross_encoder") else None,
        query=text_preview(query),
        docs=len(scored_docs),
        top_n=limit,
    )

    ranked_docs = sorted(
        scored_docs,
        key=lambda item: _safe_float(_get_field(item, "score", 0.0), default=0.0),
        reverse=True,
    )
    return ranked_docs[:limit]


@traceable(name="retrieve_shared")
def retrieve(
    query: str,
    top_k: int = 5,
    *,
    session_id: str | None = None,
    selected_skill: str | None = None,
    memory_context: list[dict[str, Any]] | None = None,
    query_image_candidates: list[Document] | None = None,
) -> list[Document]:
    """Run shared hybrid retrieval and return normalized documents."""

    trace_id = new_trace_id("search")
    query_hint = text_preview(query)
    vector_k = max(1, int(getattr(settings, "RECALL_VECTOR_TOP_K", top_k)))
    image_vector_k = max(1, int(getattr(settings, "RECALL_IMAGE_VECTOR_TOP_K", top_k)))
    web_k = max(1, int(getattr(settings, "RECALL_WEB_TOP_K", top_k)))
    keyword_k = max(1, int(getattr(settings, "RECALL_KEYWORD_TOP_K", top_k)))
    memory_k = max(1, int(getattr(settings, "RECALL_MEMORY_TOP_K", top_k)))
    rerank_top_n = max(int(top_k), int(getattr(settings, "RERANK_FINAL_TOP_N", top_k)))

    recall_tasks: list[tuple[str, Any]] = []
    if getattr(settings, "RECALL_ENABLE_VECTOR", True):
        recall_tasks.append(("vector", lambda: recall_vector(query=query, top_k=vector_k, trace_id=trace_id)))
    if getattr(settings, "RECALL_ENABLE_IMAGE_VECTOR", False):
        recall_tasks.append(
            (
                "image_vector",
                lambda: recall_image_vector(
                    query=query,
                    top_k=image_vector_k,
                    seed_documents=query_image_candidates,
                    trace_id=trace_id,
                ),
            )
        )
    if getattr(settings, "RECALL_ENABLE_WEB", True):
        recall_tasks.append(("web", lambda: recall_web(query=query, top_k=web_k, trace_id=trace_id)))
    if getattr(settings, "RECALL_ENABLE_KEYWORD", True):
        recall_tasks.append(("keyword", lambda: recall_keyword(query=query, top_k=keyword_k, trace_id=trace_id)))
    if getattr(settings, "RECALL_ENABLE_MEMORY", True):
        recall_tasks.append(
            (
                "memory",
                lambda: recall_memory(
                    query=query,
                    session_id=session_id,
                    top_k=memory_k,
                    memory_context=memory_context,
                ),
            )
        )

    channel_docs: dict[str, list[Document]] = {}
    if recall_tasks:
        with ThreadPoolExecutor(max_workers=min(5, len(recall_tasks))) as executor:
            future_map = {executor.submit(run): channel for channel, run in recall_tasks}
            for future in as_completed(future_map):
                channel = future_map[future]
                try:
                    channel_docs[channel] = future.result() or []
                except Exception as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "search.retrieve.channel.fail",
                        component="search",
                        trace_id=trace_id,
                        channel=channel,
                        query=query_hint,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    channel_docs[channel] = []

    merged_docs: list[Document] = []
    normalized_vector_docs = _normalize_documents(
        channel_docs.get("vector", []),
        source_hint="vector",
        channel="vector",
    )
    normalized_image_vector_docs = _normalize_documents(
        channel_docs.get("image_vector", []),
        source_hint="image_vector",
        channel="image_vector",
    )
    merged_docs.extend(
        _fuse_local_vector_documents(
            normalized_vector_docs,
            normalized_image_vector_docs,
        )
    )
    merged_docs.extend(_normalize_documents(channel_docs.get("web", []), source_hint="web", channel="web"))
    merged_docs.extend(_normalize_documents(channel_docs.get("keyword", []), source_hint="keyword", channel="keyword"))
    merged_docs.extend(_normalize_documents(channel_docs.get("memory", []), source_hint="memory", channel="memory"))
    merged_docs = _deduplicate_documents(merged_docs)

    merged_top5 = []
    for doc in merged_docs[:5]:
        metadata = _get_field(doc, "metadata", {}) or {}
        merged_top5.append(
            {
                "doc_id": str(_get_field(metadata, "doc_id", "") or ""),
                "score": round(_safe_float(_get_field(metadata, "score", _get_field(doc, "score", 0.0))), 6),
                "channel": str(_get_field(metadata, "channel", "") or _get_field(metadata, "retrieval_source", "")),
            }
        )

    if not settings.RETRIEVAL_ENABLE_RERANK:
        log_event(
            logger,
            logging.INFO,
            "search.retrieve.rerank.skip",
            component="search",
            trace_id=trace_id,
            query=query_hint,
            before_count=len(merged_docs),
            after_count=len(merged_docs),
            channels=_channel_counts(merged_docs),
            top5=merged_top5,
        )
        return merged_docs

    reranked_docs = rerank_documents(
        query=query,
        docs=merged_docs,
        top_n=rerank_top_n,
        selected_skill=selected_skill,
        session_id=session_id,
    )

    top5 = []
    for doc in reranked_docs[:5]:
        metadata = _get_field(doc, "metadata", {}) or {}
        top5.append(
            {
                "doc_id": str(_get_field(metadata, "doc_id", "") or ""),
                "score": round(_safe_float(_get_field(metadata, "score", _get_field(doc, "score", 0.0))), 6),
                "channel": str(_get_field(metadata, "channel", "") or _get_field(metadata, "retrieval_source", "")),
            }
        )

    log_event(
        logger,
        logging.INFO,
        "search.retrieve.rerank.done",
        component="search",
        trace_id=trace_id,
        query=query_hint,
        before_count=len(merged_docs),
        after_count=len(reranked_docs),
        channels=_channel_counts(reranked_docs),
        selected_skill=str(selected_skill or ""),
        top5=top5,
    )
    return reranked_docs

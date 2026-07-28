import logging
import threading
import time
from dataclasses import dataclass

import openai

from ..config import settings
from ..logging_utils import elapsed_ms, error_fields, log_event, log_exception, new_trace_id
from ..observability import wrap_openai

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency
    SentenceTransformer = None

logger = logging.getLogger(__name__)

_DASHSCOPE_PROVIDER = "dashscope"
_LOCAL_PROVIDER = "local"
_DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:18001/v1"
_DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DASHSCOPE_DEFAULT_MODEL = "text-embedding-v4"
_DASHSCOPE_MODEL_ALIASES = {
    "qwen/qwen3-embedding-0.6b",
    "qwen3-embedding-0.6b",
}
_LOCAL_BGE_CACHE_DIR_NAME = "models--baai--bge-small-zh"
_LOCAL_BGE_MODEL_NAME = "BAAI/bge-small-zh"

_LOCAL_ST_MODELS: dict[str, "SentenceTransformer"] = {}
_LOCAL_ST_LOCK = threading.Lock()


@dataclass(frozen=True)
class _EmbeddingAttempt:
    provider: str
    model: str
    api_key: str
    base_url: str | None


def _as_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_provider(value: object, default: str = _DASHSCOPE_PROVIDER) -> str:
    provider = str(value or "").strip().lower()
    if provider in {_DASHSCOPE_PROVIDER, _LOCAL_PROVIDER}:
        return provider
    return default


def _provider_order() -> list[str]:
    primary = _normalize_provider(getattr(settings, "EMBEDDING_PROVIDER", _DASHSCOPE_PROVIDER))
    providers = [primary]
    fallback_enabled = _as_bool(getattr(settings, "EMBEDDING_ENABLE_FALLBACK", True), default=True)
    if fallback_enabled:
        fallback = _normalize_provider(
            getattr(settings, "EMBEDDING_FALLBACK_PROVIDER", _DASHSCOPE_PROVIDER),
            default=_DASHSCOPE_PROVIDER,
        )
        if fallback not in providers:
            providers.append(fallback)
    return providers


def _resolve_embedding_api_key(provider: str) -> str:
    if provider == _DASHSCOPE_PROVIDER:
        return settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    local_key = str(getattr(settings, "EMBEDDING_LOCAL_API_KEY", "") or "").strip()
    return local_key or settings.OPENAI_API_KEY or settings.DASHSCOPE_API_KEY or "LOCAL_DUMMY_KEY"


def _resolve_embedding_base_url(provider: str) -> str | None:
    embedding_base_url = str(getattr(settings, "EMBEDDING_BASE_URL", "") or "").strip()
    openai_base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "").strip()
    shared_base_url = str(getattr(settings, "BASE_URL", "") or "").strip()
    local_base_url = str(getattr(settings, "EMBEDDING_LOCAL_BASE_URL", "") or "").strip()

    if provider == _DASHSCOPE_PROVIDER:
        return embedding_base_url or shared_base_url or openai_base_url or _DEFAULT_DASHSCOPE_BASE_URL
    return local_base_url or openai_base_url or _DEFAULT_LOCAL_BASE_URL


def _resolve_embedding_model(provider: str) -> str:
    model_name = str(getattr(settings, "EMBEDDING_MODEL", "") or "").strip()
    local_model_name = str(getattr(settings, "EMBEDDING_LOCAL_MODEL", "") or "").strip()
    if provider == _LOCAL_PROVIDER:
        return local_model_name or model_name
    if provider == _DASHSCOPE_PROVIDER:
        lowered_name = model_name.lower()
        if not model_name or lowered_name in _DASHSCOPE_MODEL_ALIASES:
            return _DASHSCOPE_DEFAULT_MODEL
    return model_name


def _resolve_embedding_timeout_seconds() -> float:
    raw = getattr(settings, "EMBEDDING_TIMEOUT_SECONDS", 15)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = 15.0
    return max(2.0, timeout)


def _local_files_only_enabled() -> bool:
    return _as_bool(getattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", True), default=True)


def _normalize_local_model_name(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    lowered = normalized.lower().replace("\\", "/")
    if lowered == _LOCAL_BGE_CACHE_DIR_NAME:
        return _LOCAL_BGE_MODEL_NAME
    return normalized


def _get_local_sentence_model(model_name: str, trace_id: str) -> "SentenceTransformer":
    if SentenceTransformer is None:
        raise RuntimeError(
            "sentence-transformers is not installed; cannot use local embedding provider"
        )

    normalized_name = _normalize_local_model_name(model_name)
    with _LOCAL_ST_LOCK:
        cached = _LOCAL_ST_MODELS.get(normalized_name)
    if cached is not None:
        log_event(
            logger,
            logging.INFO,
            "embedding.model.cache_hit",
            component="embedding",
            trace_id=trace_id,
            provider=_LOCAL_PROVIDER,
            model=normalized_name,
        )
        return cached

    local_files_only = _local_files_only_enabled()
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "embedding.model.load.start",
        component="embedding",
        trace_id=trace_id,
        provider=_LOCAL_PROVIDER,
        model=normalized_name,
        local_files_only=local_files_only,
    )
    try:
        model = SentenceTransformer(
            normalized_name,
            local_files_only=local_files_only,
        )
    except Exception as exc:
        log_exception(
            logger,
            "embedding.model.load.fail",
            component="embedding",
            trace_id=trace_id,
            provider=_LOCAL_PROVIDER,
            model=normalized_name,
            elapsed_ms=elapsed_ms(started_at),
            **error_fields(exc),
        )
        raise

    with _LOCAL_ST_LOCK:
        _LOCAL_ST_MODELS[normalized_name] = model
    log_event(
        logger,
        logging.INFO,
        "embedding.model.load.done",
        component="embedding",
        trace_id=trace_id,
        provider=_LOCAL_PROVIDER,
        model=normalized_name,
        elapsed_ms=elapsed_ms(started_at),
    )
    return model


def _embed_texts_local(texts: list[str], model_name: str, trace_id: str) -> list[list[float]]:
    model = _get_local_sentence_model(model_name, trace_id=trace_id)
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "embedding.local.encode.start",
        component="embedding",
        trace_id=trace_id,
        provider=_LOCAL_PROVIDER,
        model=model_name,
        text_count=len(texts),
    )
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    converted = [[float(value) for value in vector] for vector in vectors]
    vector_dim = len(converted[0]) if converted and converted[0] else 0
    log_event(
        logger,
        logging.INFO,
        "embedding.local.encode.done",
        component="embedding",
        trace_id=trace_id,
        provider=_LOCAL_PROVIDER,
        model=model_name,
        elapsed_ms=elapsed_ms(started_at),
        vectors=len(converted),
        dimension=vector_dim,
    )
    return converted


def _build_embedding_attempts(trace_id: str) -> list[_EmbeddingAttempt]:
    attempts: list[_EmbeddingAttempt] = []
    for provider in _provider_order():
        model_name = _resolve_embedding_model(provider)
        if not model_name:
            log_event(
                logger,
                logging.WARNING,
                "embedding.config.invalid",
                component="embedding",
                trace_id=trace_id,
                provider=provider,
                reason="model_missing",
            )
            continue
        attempts.append(
            _EmbeddingAttempt(
                provider=provider,
                model=model_name,
                api_key=_resolve_embedding_api_key(provider),
                base_url=_resolve_embedding_base_url(provider),
            )
        )
    return attempts


def _get_openai_client(attempt: _EmbeddingAttempt) -> openai.OpenAI:
    return wrap_openai(openai.OpenAI(
        api_key=attempt.api_key,
        base_url=attempt.base_url,
        timeout=_resolve_embedding_timeout_seconds(),
        max_retries=0,
    ))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts with the configured embedding model."""
    trace_id = new_trace_id("embedding")
    clean_texts = [str(text).strip() for text in (texts or []) if str(text).strip()]
    if not clean_texts:
        log_event(
            logger,
            logging.INFO,
            "embedding.request.skip",
            component="embedding",
            trace_id=trace_id,
            reason="empty_input",
        )
        return []

    attempts = _build_embedding_attempts(trace_id=trace_id)
    if not attempts:
        log_event(
            logger,
            logging.ERROR,
            "embedding.request.invalid_config",
            component="embedding",
            trace_id=trace_id,
            reason="no_provider_model_configured",
            text_count=len(clean_texts),
        )
        raise ValueError("No embedding provider/model is configured")

    log_event(
        logger,
        logging.INFO,
        "embedding.request.start",
        component="embedding",
        trace_id=trace_id,
        text_count=len(clean_texts),
        providers=[attempt.provider for attempt in attempts],
    )

    last_error: Exception | None = None
    for idx, attempt in enumerate(attempts, start=1):
        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "embedding.invoke.start",
            component="embedding",
            trace_id=trace_id,
            provider=attempt.provider,
            model=attempt.model,
            attempt=idx,
            attempt_total=len(attempts),
            text_count=len(clean_texts),
            base_url=attempt.base_url,
        )
        try:
            if attempt.provider == _LOCAL_PROVIDER:
                vectors = _embed_texts_local(clean_texts, attempt.model, trace_id=trace_id)
            else:
                client = _get_openai_client(attempt)
                remote_started_at = time.perf_counter()
                log_event(
                    logger,
                    logging.INFO,
                    "embedding.remote.request.start",
                    component="embedding",
                    trace_id=trace_id,
                    provider=attempt.provider,
                    model=attempt.model,
                    attempt=idx,
                    attempt_total=len(attempts),
                )
                response = client.embeddings.create(
                    model=attempt.model,
                    input=clean_texts,
                )
                vectors = [item.embedding for item in response.data]
                log_event(
                    logger,
                    logging.INFO,
                    "embedding.remote.request.done",
                    component="embedding",
                    trace_id=trace_id,
                    provider=attempt.provider,
                    model=attempt.model,
                    attempt=idx,
                    attempt_total=len(attempts),
                    elapsed_ms=elapsed_ms(remote_started_at),
                    vectors=len(vectors),
                )

            vector_dim = len(vectors[0]) if vectors and vectors[0] else 0
            log_event(
                logger,
                logging.INFO,
                "embedding.invoke.done",
                component="embedding",
                trace_id=trace_id,
                provider=attempt.provider,
                model=attempt.model,
                attempt=idx,
                attempt_total=len(attempts),
                elapsed_ms=elapsed_ms(started_at),
                vectors=len(vectors),
                dimension=vector_dim,
            )
            log_event(
                logger,
                logging.INFO,
                "embedding.request.done",
                component="embedding",
                trace_id=trace_id,
                provider=attempt.provider,
                model=attempt.model,
                vectors=len(vectors),
                dimension=vector_dim,
            )
            return vectors
        except Exception as exc:
            last_error = exc
            log_event(
                logger,
                logging.WARNING,
                "embedding.invoke.fail",
                component="embedding",
                trace_id=trace_id,
                provider=attempt.provider,
                model=attempt.model,
                attempt=idx,
                attempt_total=len(attempts),
                elapsed_ms=elapsed_ms(started_at),
                will_fallback=idx < len(attempts),
                **error_fields(exc),
            )

    if last_error is not None:
        log_event(
            logger,
            logging.ERROR,
            "embedding.request.fail",
            component="embedding",
            trace_id=trace_id,
            providers=[attempt.provider for attempt in attempts],
            text_count=len(clean_texts),
            **error_fields(last_error),
        )
        raise last_error

    log_event(
        logger,
        logging.ERROR,
        "embedding.request.fail",
        component="embedding",
        trace_id=trace_id,
        providers=[attempt.provider for attempt in attempts],
        text_count=len(clean_texts),
        status="error",
        error="embedding request failed without captured exception",
    )
    raise RuntimeError("embedding request failed without captured exception")

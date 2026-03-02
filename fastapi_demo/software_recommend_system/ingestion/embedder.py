from typing import List
import logging

import openai

from ..config import settings

logger = logging.getLogger(__name__)

_DASHSCOPE_PROVIDER = "dashscope"
_DASHSCOPE_DEFAULT_MODEL = "text-embedding-v4"
_DASHSCOPE_MODEL_ALIASES = {
    "qwen/qwen3-embedding-0.6b",
    "qwen3-embedding-0.6b",
}


def _embedding_provider() -> str:
    return str(getattr(settings, "EMBEDDING_PROVIDER", "") or "").strip().lower()


def _resolve_embedding_api_key(provider: str) -> str:
    if provider == _DASHSCOPE_PROVIDER:
        return settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    return settings.OPENAI_API_KEY or settings.DASHSCOPE_API_KEY


def _resolve_embedding_base_url(provider: str) -> str | None:
    embedding_base_url = str(getattr(settings, "EMBEDDING_BASE_URL", "") or "").strip()
    openai_base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "").strip()
    shared_base_url = str(getattr(settings, "BASE_URL", "") or "").strip()

    if provider == _DASHSCOPE_PROVIDER:
        return embedding_base_url or openai_base_url or shared_base_url or None
    return openai_base_url or embedding_base_url or shared_base_url or None


def _resolve_embedding_model(provider: str) -> str:
    model_name = str(settings.EMBEDDING_MODEL or "").strip()
    if provider == _DASHSCOPE_PROVIDER:
        lowered_name = model_name.lower()
        if not model_name or lowered_name in _DASHSCOPE_MODEL_ALIASES:
            return _DASHSCOPE_DEFAULT_MODEL
    return model_name


def _get_openai_client() -> openai.OpenAI:
    provider = _embedding_provider()
    api_key = _resolve_embedding_api_key(provider)
    base_url = _resolve_embedding_base_url(provider)
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed texts with the configured embedding model."""
    clean_texts = [str(text).strip() for text in (texts or []) if str(text).strip()]
    if not clean_texts:
        logger.info("embedding skipped: no non-empty texts")
        return []

    provider = _embedding_provider()
    model_name = _resolve_embedding_model(provider)
    if not model_name:
        raise ValueError("EMBEDDING_MODEL is not configured")

    logger.info(
        "embedding request start: provider=%s model=%s texts=%d",
        provider,
        model_name,
        len(clean_texts),
    )
    client = _get_openai_client()
    try:
        response = client.embeddings.create(
            model=model_name,
            input=clean_texts,
        )
    except Exception:
        logger.exception(
            "embedding request failed: provider=%s model=%s texts=%d",
            provider,
            model_name,
            len(clean_texts),
        )
        raise

    vectors = [item.embedding for item in response.data]
    vector_dim = len(vectors[0]) if vectors and vectors[0] else 0
    logger.info(
        "embedding request success: vectors=%d dimension=%d",
        len(vectors),
        vector_dim,
    )
    return vectors

from typing import List

import openai

from ..config import settings


def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed texts with the configured embedding model."""
    clean_texts = [str(text).strip() for text in (texts or []) if str(text).strip()]
    if not clean_texts:
        return []

    client = _get_openai_client()
    response = client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=clean_texts,
    )
    return [item.embedding for item in response.data]

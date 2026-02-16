from typing import Any, Dict, List, Sequence

import chromadb

from ..config import settings

DEFAULT_COLLECTION_NAME = "software_recommendations"


def get_chroma_collection(collection_name: str = DEFAULT_COLLECTION_NAME):
    """Load or create the configured persistent Chroma collection."""
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    return client.get_or_create_collection(collection_name)


def index_embeddings(
    chunk_ids: Sequence[str],
    chunk_texts: Sequence[str],
    metadatas: Sequence[Dict[str, Any]],
    embeddings: Sequence[Sequence[float]],
    collection_name: str = DEFAULT_COLLECTION_NAME,
) -> int:
    """Write chunk records into Chroma and return indexed count."""
    ids = [str(item) for item in chunk_ids]
    docs = [str(item) for item in chunk_texts]
    metas: List[Dict[str, Any]] = [dict(item or {}) for item in metadatas]
    vectors = [list(vector) for vector in embeddings]

    if not ids:
        return 0

    expected_len = len(ids)
    if not (
        len(docs) == expected_len
        and len(metas) == expected_len
        and len(vectors) == expected_len
    ):
        raise ValueError("ids, texts, metadatas, and embeddings length mismatch")

    collection = get_chroma_collection(collection_name=collection_name)
    collection.upsert(
        ids=ids,
        documents=docs,
        metadatas=metas,
        embeddings=vectors,
    )
    return expected_len

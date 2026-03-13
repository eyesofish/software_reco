from typing import Any, Dict, List, Sequence

import chromadb

from ..config import settings

DEFAULT_COLLECTION_NAME = "software_recommendations"
DEFAULT_PARENT_COLLECTION_NAME = "software_recommendations_parent"


def get_chroma_collection(collection_name: str = DEFAULT_COLLECTION_NAME):
    """Load or create the configured persistent Chroma collection."""
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    return client.get_or_create_collection(collection_name)


def get_parent_collection(collection_name: str = DEFAULT_PARENT_COLLECTION_NAME):
    """Load or create the configured parent-document collection."""
    parent_name = collection_name or getattr(settings, "PARENT_COLLECTION_NAME", DEFAULT_PARENT_COLLECTION_NAME)
    return get_chroma_collection(collection_name=parent_name)


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


def index_parent_documents(
    parent_ids: Sequence[str],
    parent_texts: Sequence[str],
    metadatas: Sequence[Dict[str, Any]],
    collection_name: str = DEFAULT_PARENT_COLLECTION_NAME,
) -> int:
    """Write parent chunk records into Chroma and return indexed count."""
    ids = [str(item) for item in parent_ids]
    docs = [str(item) for item in parent_texts]
    metas: List[Dict[str, Any]] = [dict(item or {}) for item in metadatas]

    if not ids:
        return 0

    expected_len = len(ids)
    if not (len(docs) == expected_len and len(metas) == expected_len):
        raise ValueError("parent ids, texts, and metadatas length mismatch")

    collection = get_parent_collection(collection_name=collection_name)
    collection.upsert(
        ids=ids,
        documents=docs,
        metadatas=metas,
    )
    return expected_len


def _flatten_chroma_values(values: Any) -> List[Any]:
    if isinstance(values, list):
        if values and isinstance(values[0], list):
            return list(values[0])
        return values
    return []


def get_parent_documents_by_ids(
    parent_ids: Sequence[str],
    collection_name: str = DEFAULT_PARENT_COLLECTION_NAME,
) -> Dict[str, Dict[str, Any]]:
    """Fetch parent chunk content and metadata by parent IDs."""
    ids = [str(item).strip() for item in parent_ids if str(item).strip()]
    if not ids:
        return {}

    collection = get_parent_collection(collection_name=collection_name)
    try:
        raw = collection.get(ids=ids, include=["documents", "metadatas"])
    except TypeError:
        raw = collection.get(ids=ids)

    result_ids = _flatten_chroma_values((raw or {}).get("ids"))
    documents = _flatten_chroma_values((raw or {}).get("documents"))
    metadatas = _flatten_chroma_values((raw or {}).get("metadatas"))

    output: Dict[str, Dict[str, Any]] = {}
    for index, parent_id in enumerate(result_ids):
        parent_key = str(parent_id)
        output[parent_key] = {
            "id": parent_key,
            "content": str(documents[index]) if index < len(documents) else "",
            "metadata": dict(metadatas[index] or {}) if index < len(metadatas) else {},
        }
    return output

import hashlib
import re
from collections.abc import Sequence
from typing import Any

import chromadb

from ..config import settings
from ..image_embedder import image_embedding_identity
from ..keyword_index import mirror_chunks

DEFAULT_COLLECTION_NAME = "software_recommendations"
DEFAULT_PARENT_COLLECTION_NAME = "software_recommendations_parent"
DEFAULT_IMAGE_COLLECTION_NAME = "software_recommendations_image"


def get_chroma_collection(collection_name: str = DEFAULT_COLLECTION_NAME):
    """Load or create the configured persistent Chroma collection."""
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    return client.get_or_create_collection(collection_name)


def get_parent_collection(collection_name: str = DEFAULT_PARENT_COLLECTION_NAME):
    """Load or create the configured parent-document collection."""
    parent_name = collection_name or getattr(settings, "PARENT_COLLECTION_NAME", DEFAULT_PARENT_COLLECTION_NAME)
    return get_chroma_collection(collection_name=parent_name)


def resolve_image_collection_name(collection_name: str | None = None) -> str:
    """Version the configured image collection by embedding-space identity."""
    explicit_name = str(collection_name or "").strip()
    if explicit_name:
        return explicit_name

    base_name = (
        str(getattr(settings, "IMAGE_VECTOR_COLLECTION_NAME", DEFAULT_IMAGE_COLLECTION_NAME)).strip()
        or DEFAULT_IMAGE_COLLECTION_NAME
    )
    safe_base = re.sub(r"[^A-Za-z0-9._-]", "_", base_name).strip("._-")
    safe_base = safe_base or DEFAULT_IMAGE_COLLECTION_NAME
    model_digest = hashlib.sha256(image_embedding_identity().encode("utf-8")).hexdigest()[:12]
    max_base_length = 512 - len(model_digest) - 1
    return f"{safe_base[:max_base_length]}_{model_digest}"


def get_image_collection(collection_name: str | None = None) -> Any:
    """Load or create the cosine collection used by native image embeddings."""
    image_name = resolve_image_collection_name(collection_name)
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    return client.get_or_create_collection(
        image_name,
        metadata={"hnsw:space": "cosine"},
    )


def index_embeddings(
    chunk_ids: Sequence[str],
    chunk_texts: Sequence[str],
    metadatas: Sequence[dict[str, Any]],
    embeddings: Sequence[Sequence[float]],
    collection_name: str = DEFAULT_COLLECTION_NAME,
    *,
    sync_keyword: bool = True,
) -> int:
    """Write chunk records into Chroma and return indexed count."""
    ids = [str(item) for item in chunk_ids]
    docs = [str(item) for item in chunk_texts]
    metas: list[dict[str, Any]] = [dict(item or {}) for item in metadatas]
    vectors = [list(vector) for vector in embeddings]

    if not ids:
        return 0

    expected_len = len(ids)
    if not (len(docs) == expected_len and len(metas) == expected_len and len(vectors) == expected_len):
        raise ValueError("ids, texts, metadatas, and embeddings length mismatch")

    collection = get_chroma_collection(collection_name=collection_name)
    _write_records(collection, ids, docs, metas, vectors)
    if sync_keyword and collection_name == DEFAULT_COLLECTION_NAME:
        mirror_chunks(ids, docs, metas)
    return expected_len


def index_image_embeddings(
    image_ids: Sequence[str],
    captions: Sequence[str],
    metadatas: Sequence[dict[str, Any]],
    embeddings: Sequence[Sequence[float]],
    collection_name: str | None = None,
) -> int:
    """Write one shared-space vector per knowledge image."""
    ids = [str(item) for item in image_ids]
    docs = [str(item) for item in captions]
    metas: list[dict[str, Any]] = [dict(item or {}) for item in metadatas]
    vectors = [list(vector) for vector in embeddings]

    if not ids:
        return 0

    expected_len = len(ids)
    if not (len(docs) == expected_len and len(metas) == expected_len and len(vectors) == expected_len):
        raise ValueError("image ids, captions, metadatas, and embeddings length mismatch")

    collection = get_image_collection(collection_name=collection_name)
    _write_records(collection, ids, docs, metas, vectors)
    return expected_len


def index_parent_documents(
    parent_ids: Sequence[str],
    parent_texts: Sequence[str],
    metadatas: Sequence[dict[str, Any]],
    collection_name: str = DEFAULT_PARENT_COLLECTION_NAME,
) -> int:
    """Write parent chunk records into Chroma and return indexed count."""
    ids = [str(item) for item in parent_ids]
    docs = [str(item) for item in parent_texts]
    metas: list[dict[str, Any]] = [dict(item or {}) for item in metadatas]

    if not ids:
        return 0

    expected_len = len(ids)
    if not (len(docs) == expected_len and len(metas) == expected_len):
        raise ValueError("parent ids, texts, and metadatas length mismatch")

    collection = get_parent_collection(collection_name=collection_name)
    _write_records(collection, ids, docs, metas)
    return expected_len


def _flatten_chroma_values(values: Any) -> list[Any]:
    if isinstance(values, list):
        if values and isinstance(values[0], list):
            return list(values[0])
        return values
    return []


def get_parent_documents_by_ids(
    parent_ids: Sequence[str],
    collection_name: str = DEFAULT_PARENT_COLLECTION_NAME,
) -> dict[str, dict[str, Any]]:
    """Fetch parent chunk content and metadata by parent IDs."""
    ids = [str(item).strip() for item in parent_ids if str(item).strip()]
    if not ids:
        return {}

    collection = get_parent_collection(collection_name=collection_name)
    from ..kb_versions import chroma_filter, visible

    where = chroma_filter()
    query = {"ids": ids, "include": ["documents", "metadatas"]}
    if where is not None:
        query["where"] = where
    try:
        raw = collection.get(**query)
    except TypeError:
        if where is not None:
            raise
        raw = collection.get(ids=ids)

    result_ids = _flatten_chroma_values((raw or {}).get("ids"))
    documents = _flatten_chroma_values((raw or {}).get("documents"))
    metadatas = _flatten_chroma_values((raw or {}).get("metadatas"))

    output: dict[str, dict[str, Any]] = {}
    for index, parent_id in enumerate(result_ids):
        parent_key = str(parent_id)
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
        if not visible(metadata):
            continue
        output[parent_key] = {
            "id": parent_key,
            "content": str(documents[index]) if index < len(documents) else "",
            "metadata": dict(metadatas[index] or {}) if index < len(metadatas) else {},
        }
    return output


def _all_rows(collection: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    count = int(collection.count())
    for offset in range(0, count, 500):
        page = collection.get(limit=500, offset=offset, include=["documents", "metadatas"])
        ids = list(page.get("ids") or [])
        documents = list(page.get("documents") or [])
        metadatas = list(page.get("metadatas") or [])
        rows.extend(
            {
                "id": str(row_id),
                "content": documents[i] if i < len(documents) else "",
                "metadata": dict(metadatas[i] or {}) if i < len(metadatas) else {},
            }
            for i, row_id in enumerate(ids)
        )
    if len(rows) != count or int(collection.count()) != count:
        raise RuntimeError("KB changed during legacy adoption; retry with old ingestion stopped")
    return rows


def _adopt_legacy_rows() -> dict[str, str]:
    """Decorate existing rows without replacing content, vectors, or IDs."""
    import json
    from pathlib import Path

    from ..kb_versions import LEGACY, VERSION_FIELDS, document_identity, preserve_asset

    stores = [("text", get_chroma_collection()), ("parent", get_parent_collection(settings.PARENT_COLLECTION_NAME))]
    # Adopt an existing native image collection even if image recall is disabled.
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    image_name = resolve_image_collection_name()
    if image_name in {str(getattr(item, "name", item)) for item in client.list_collections()}:
        stores.append(("image", client.get_collection(image_name)))
    batches: list[tuple[str, Any, list[dict[str, Any]]]] = []
    grouped: dict[str, list[Any]] = {}
    for kind, collection in stores:
        records = [
            row
            for row in _all_rows(collection)
            if not row["metadata"].get("version") or str(row["metadata"].get("version")).startswith("legacy-")
        ]
        batches.append((kind, collection, records))
        for row in records:
            metadata = row["metadata"]
            logical = document_identity(metadata, row["id"])
            row["logical"] = logical
            # Exclude adopted provenance and rewritten asset URL so retries hash identically.
            clean = {
                key: value for key, value in metadata.items() if key not in {*VERSION_FIELDS, "asset_path", "asset_url"}
            }
            grouped.setdefault(logical, []).append((kind, row["id"], row["content"], clean))
    versions = {
        logical: "legacy-"
        + hashlib.sha256(
            json.dumps(sorted(values, key=str), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        for logical, values in grouped.items()
    }
    ingest_root = Path(getattr(settings, "INGEST_PATH", "ingest_docs"))
    for kind, collection, records in batches:
        for row in records:
            metadata = row["metadata"]
            logical = row["logical"]
            metadata.update(
                {
                    "document_id": logical,
                    "version": versions[logical],
                    "snapshot_id": LEGACY,
                    "status": "ready",
                    "chunk_id": row["id"],
                }
            )
            asset = str(metadata.get("asset_path") or "")
            if asset and not asset.startswith(".versions/"):
                source = (ingest_root / asset).resolve()
                if not source.is_relative_to(ingest_root.resolve()) or not source.is_file():
                    raise ValueError("Cannot retain missing or unsafe legacy image asset")
                metadata["asset_path"] = preserve_asset(
                    source.read_bytes(), source.name, logical, versions[logical], ingest_root
                )
                metadata.pop("asset_url", None)
        for start in range(0, len(records), 500):
            page = records[start : start + 500]
            collection.update(ids=[r["id"] for r in page], metadatas=[r["metadata"] for r in page])
            if kind == "text" and settings.RECALL_ENABLE_KEYWORD:
                from ..keyword_index import get_keyword_index

                keyword = get_keyword_index()
                keyword.upsert([r["id"] for r in page], [r["content"] for r in page], [r["metadata"] for r in page])
                keyword.client.indices.refresh(index=keyword.index)
    return versions


def publish_documents(
    documents: list[dict[str, Any]], *, image_sources: dict[str, Any] | None = None, ingest_root: Any = None
) -> dict[str, Any]:
    """Stage immutable revisions in every enabled store, then atomically publish.

    A failed stage is unreachable to readers; existing rows are never pruned.
    Concurrent publications use compare-and-swap and cannot lose an update.
    """
    from pathlib import Path

    from ..image_embedder import embed_image_files
    from ..kb_versions import get_store, preserve_asset, revision, versioned_document
    from .chunker import chunk_documents, chunk_documents_parent_child
    from .embedder import embed_texts
    from .loader import normalize_documents

    normalized = normalize_documents(documents)
    if not normalized:
        raise ValueError("No documents to publish")
    identities = [str(doc["id"]) for doc in normalized]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate logical document identity in publication")
    store = get_store()
    store.adopt_legacy(_adopt_legacy_rows)
    active = store.active()
    previous = store.members(active) or {}
    image_sources = dict(image_sources or {})
    # Preserve adopted logical identities and reject aliases for a different path.
    text_collection = get_chroma_collection()
    for doc in normalized:
        path = str(doc["metadata"].get("path") or "")
        if path:
            existing = text_collection.get(where={"path": path}, include=["metadatas"])
            owners = {
                str(meta.get("document_id"))
                for meta in existing.get("metadatas", [])
                if meta and meta.get("document_id")
            }
            if len(owners) > 1:
                raise ValueError("Source has conflicting document identities")
            original = str(doc["id"])
            if owners:
                doc["id"] = next(iter(owners))
                if original in image_sources:
                    image_sources[str(doc["id"])] = image_sources.pop(original)
            identity_rows = text_collection.get(where={"document_id": str(doc["id"])}, include=["metadatas"])
            if any(str((meta or {}).get("path") or "") != path for meta in identity_rows.get("metadatas", [])):
                raise ValueError("Document identity belongs to another source")
    image_enabled = bool(getattr(settings, "RECALL_ENABLE_IMAGE_VECTOR", False))
    policy = {
        name: getattr(settings, name, None)
        for name in (
            "CHUNKING_STRATEGY",
            "CHUNK_SIZE",
            "CHUNK_OVERLAP",
            "ENABLE_PARENT_CHILD_CHUNKING",
            "PARENT_CHUNK_SIZE",
            "PARENT_CHUNK_OVERLAP",
            "CHILD_CHUNK_SIZE",
            "CHILD_CHUNK_OVERLAP",
            "EMBEDDING_MODEL",
            "EMBEDDING_PROVIDER",
            "EMBEDDING_LOCAL_MODEL",
            "EMBEDDING_ENABLE_FALLBACK",
            "EMBEDDING_FALLBACK_PROVIDER",
            "RECALL_ENABLE_IMAGE_VECTOR",
            "RECALL_ENABLE_KEYWORD",
        )
    }
    policy["pipeline_version"] = "kb-mvcc.v1"
    if image_enabled and image_sources:
        policy["image_embedding_identity"] = image_embedding_identity()
    root = Path(ingest_root or getattr(settings, "INGEST_PATH", "ingest_docs"))
    prepared: list[tuple[dict[str, Any], str, bytes | None]] = []
    for doc in normalized:
        logical = str(doc["id"])
        source = image_sources.get(logical)
        data = Path(source).read_bytes() if source is not None else None
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            expected = str(doc["metadata"].get("source_sha256") or "")
            if expected and expected != digest:
                raise ValueError("Image changed after caption extraction; retry publication")
            doc["metadata"] = {**doc["metadata"], "source_sha256": digest}
        version = revision(doc, policy)
        if previous.get(logical) != version:
            prepared.append((doc, version, data))
    if not prepared:
        return {"snapshot_id": active, "documents": 0, "chunks": 0, "parents": 0, "images": 0}
    required: dict[str, set[str]] = {}
    for doc, _, data in prepared:
        kinds = {"text"}
        if settings.ENABLE_PARENT_CHILD_CHUNKING:
            kinds.add("parent")
        if settings.RECALL_ENABLE_KEYWORD:
            kinds.add("keyword")
        if data is not None:
            kinds.add("asset")
            if image_enabled:
                kinds.add("image")
        required[str(doc["id"])] = kinds
    snapshot = store.stage({str(doc["id"]): version for doc, version, _ in prepared}, required, expected_base=active)
    totals = {"snapshot_id": snapshot, "documents": len(prepared), "chunks": 0, "parents": 0, "images": 0}
    try:
        for raw, version, data in prepared:
            logical = str(raw["id"])
            doc = versioned_document(raw, version, snapshot)
            immutable_asset = None
            if data is not None:
                source = Path(image_sources[logical])
                asset = preserve_asset(data, source.name, logical, version, root)
                doc["metadata"]["asset_path"] = asset
                doc["metadata"].pop("asset_url", None)
                immutable_asset = root / asset
                store.mark_ready(snapshot, logical, "asset")
            if settings.ENABLE_PARENT_CHILD_CHUNKING:
                parents, chunks = chunk_documents_parent_child(
                    [doc],
                    settings.PARENT_CHUNK_SIZE,
                    settings.PARENT_CHUNK_OVERLAP,
                    settings.CHILD_CHUNK_SIZE,
                    settings.CHILD_CHUNK_OVERLAP,
                )
            else:
                parents = []
                chunks = chunk_documents([doc], settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
            if not chunks or (settings.ENABLE_PARENT_CHILD_CHUNKING and not parents):
                raise ValueError("No chunks generated; existing snapshot retained")
            texts = [chunk["content"] for chunk in chunks]
            vectors = embed_texts(texts)
            if len(vectors) != len(chunks) or any(not vector for vector in vectors):
                raise ValueError("Incomplete embeddings; existing snapshot retained")
            if parents:
                totals["parents"] += index_parent_documents(
                    [c["id"] for c in parents],
                    [c["content"] for c in parents],
                    [c["metadata"] for c in parents],
                    settings.PARENT_COLLECTION_NAME,
                )
                store.mark_ready(snapshot, logical, "parent")
            ids, metas = [c["id"] for c in chunks], [c["metadata"] for c in chunks]
            totals["chunks"] += index_embeddings(ids, texts, metas, vectors, sync_keyword=False)
            store.mark_ready(snapshot, logical, "text")
            if settings.RECALL_ENABLE_KEYWORD:
                from ..keyword_index import get_keyword_index

                keyword = get_keyword_index()
                keyword.upsert(ids, texts, metas)
                keyword.client.indices.refresh(index=keyword.index)
                store.mark_ready(snapshot, logical, "keyword")
            if immutable_asset is not None and image_enabled:
                image_id = f"{doc['id']}:image:0"
                meta = {
                    **doc["metadata"],
                    "chunk_id": image_id,
                    "doc_id": logical,
                    "image_embedding_model": image_embedding_identity(),
                }
                image_vectors = embed_image_files([immutable_asset])
                if len(image_vectors) != 1 or not image_vectors[0]:
                    raise ValueError("Incomplete native image embedding")
                totals["images"] += index_image_embeddings([image_id], [doc["content"]], [meta], image_vectors)
                store.mark_ready(snapshot, logical, "image")
        store.publish(snapshot)
        return totals
    except BaseException:
        store.fail(snapshot)
        raise


def _write_records(
    collection: Any,
    ids: list[str],
    docs: list[str],
    metas: list[dict[str, Any]],
    vectors: list[list[float]] | None = None,
) -> None:
    payload = {"ids": ids, "documents": docs, "metadatas": metas}
    if vectors is not None:
        payload["embeddings"] = vectors
    if not any(meta.get("version") for meta in metas):
        collection.upsert(**payload)  # Legacy low-level callers; publication uses immutable rows.
        return
    existing = collection.get(ids=ids, include=["documents", "metadatas"])
    old = {
        str(key): (text, meta)
        for key, text, meta in zip(
            existing.get("ids") or [], existing.get("documents") or [], existing.get("metadatas") or [], strict=True
        )
    }
    keep = []
    for i, row_id in enumerate(ids):
        if row_id not in old:
            keep.append(i)
            continue
        text, meta = old[row_id]

        def comparable(value):
            return {key: item for key, item in dict(value or {}).items() if key != "snapshot_id"}

        if text != docs[i] or comparable(meta) != comparable(metas[i]):
            raise ValueError("Immutable chunk identity collision")
        metas[i] = dict(meta)
    if keep:
        # Chroma add does not overwrite an ID concurrently added by another stage.
        collection.add(**{key: [value[i] for i in keep] for key, value in payload.items()})

"""Preview by default; explicitly replace selected sources with --apply.

Run with ingestion paused. Chroma/ES cannot commit atomically: incomplete writes
are retryable, and the existing sync_keyword_index command repairs the ES mirror.
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..config import settings
from .chunker import chunk_documents, chunk_documents_parent_child
from .loader import normalize_documents

logger = logging.getLogger(__name__)


def resolve_paths(root: Path, paths: list[str]) -> list[Path]:
    root = root.resolve(strict=True)
    resolved = []
    for value in paths:
        path = (root / value).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Path must be a file within INGEST_PATH: {value}")
        if path.suffix.lower() not in {".md", ".pdf", ".txt"}:
            raise ValueError(f"Unsupported rechunk file type: {path.suffix}")
        if path not in resolved:
            resolved.append(path)
    return resolved


def load_document(path: Path, root: Path) -> dict[str, Any]:
    from app.api.v1.startup_ingest import _build_raw_document, _read_file_content

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    content, metadata = _read_file_content(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if before != after:
        raise ValueError("Source changed during parsing; retry with ingestion paused")
    if not content.strip():
        raise ValueError(f"No extractable content in {path.name}; scanned PDF pages require OCR")
    raw = _build_raw_document(path, root, content, set(), metadata)
    raw["metadata"]["source_sha256"] = before
    return normalize_documents([raw])[0]


def make_chunks(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if settings.ENABLE_PARENT_CHILD_CHUNKING:
        return chunk_documents_parent_child(
            [doc],
            settings.PARENT_CHUNK_SIZE,
            settings.PARENT_CHUNK_OVERLAP,
            settings.CHILD_CHUNK_SIZE,
            settings.CHILD_CHUNK_OVERLAP,
        )
    return [], chunk_documents([doc], settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)


def preview(doc: dict[str, Any]) -> dict[str, Any]:
    parents, chunks = make_chunks(doc)
    return {
        "status": "preview",
        "path": doc["metadata"]["path"],
        "strategy": settings.CHUNKING_STRATEGY,
        "parents": len(parents),
        "chunks": len(chunks),
        "warnings": doc["metadata"].get("parse_warnings", ""),
        "pieces": [
            {
                "id": c["id"],
                "length": len(c["content"]),
                **{
                    k: v
                    for k, v in c["metadata"].items()
                    if k
                    in {
                        "section_path",
                        "page_start",
                        "page_end",
                        "block_kind",
                        "table_id",
                        "table_row_start",
                        "table_row_end",
                        "oversized",
                        "fallback_reason",
                    }
                },
            }
            for c in chunks
        ],
    }


def apply_document(doc: dict[str, Any]) -> dict[str, Any]:
    from ..keyword_index import get_keyword_index
    from .embedder import embed_texts
    from .indexer import get_chroma_collection, get_parent_collection, index_embeddings, index_parent_documents

    collection = get_chroma_collection()
    parents_collection = get_parent_collection(settings.PARENT_COLLECTION_NAME)
    relative = doc["metadata"]["path"]
    # Strict lookups: a read failure must never be interpreted as an empty source.
    old = collection.get(where={"path": relative}, include=["metadatas"])
    old_parents = parents_collection.get(where={"path": relative}, include=["metadatas"])
    identities = {
        str(m["source_doc_id"])
        for m in [*(old.get("metadatas") or []), *(old_parents.get("metadatas") or [])]
        if m and m.get("source_doc_id")
    }
    if len(identities) > 1:
        raise ValueError("Source has conflicting document identities; refusing to overwrite")
    doc = {**doc, "id": next(iter(identities)) if identities else doc["id"]}
    for store in (collection, parents_collection):
        owners = store.get(where={"source_doc_id": doc["id"]}, include=["metadatas"])
        if any((meta or {}).get("path") != relative for meta in owners.get("metadatas", [])):
            raise ValueError("Document identity belongs to another source; refusing to overwrite")
    parents, chunks = make_chunks(doc)
    if not chunks:
        raise ValueError("No chunks generated; existing source retained")
    for store, records in ((collection, chunks), (parents_collection, parents)):
        if records:
            existing = store.get(ids=[c["id"] for c in records], include=["metadatas"])
            if any((m or {}).get("path") != relative for m in existing.get("metadatas", [])):
                raise ValueError("Chunk identity belongs to another source; refusing to overwrite")
    texts = [c["content"] for c in chunks]
    embeddings = embed_texts(texts)  # Compute child vectors before changing existing records.
    if len(embeddings) != len(chunks) or any(not vector for vector in embeddings):
        raise ValueError("Incomplete embeddings; existing source retained")
    parent_ids = [c["id"] for c in parents]
    ids = [c["id"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    if parents:
        index_parent_documents(
            parent_ids,
            [c["content"] for c in parents],
            [c["metadata"] for c in parents],
            settings.PARENT_COLLECTION_NAME,
        )
    index_embeddings(ids, texts, metadatas, embeddings, sync_keyword=False)
    stale = sorted(set(old.get("ids", [])) - set(ids))
    stale_parents = sorted(set(old_parents.get("ids", [])) - set(parent_ids))
    if settings.RECALL_ENABLE_KEYWORD:
        try:
            index = get_keyword_index()
            index.upsert(ids, texts, metadatas)
            index.delete(stale)
        except Exception as exc:
            # Keep old Chroma IDs until ES succeeds, so retries still know what to remove.
            raise RuntimeError(
                "New Chroma chunks written; ES sync failed. Retry --apply to finish replacement. "
                "python -m software_recommend_system.ingestion.sync_keyword_index repairs only the ES mirror."
            ) from exc
    if stale:
        collection.delete(ids=stale)
    if stale_parents:
        parents_collection.delete(ids=stale_parents)
    return {
        "status": "applied",
        "path": relative,
        "chunks": len(chunks),
        "parents": len(parents),
        "removed_chunks": len(stale),
        "removed_parents": len(stale_parents),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", required=True, help="File relative to INGEST_PATH; repeatable")
    parser.add_argument("--apply", action="store_true", help="Write indexes; pause ingestion first")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    root = Path(settings.INGEST_PATH).resolve()
    try:
        paths = resolve_paths(root, args.path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    failed = False
    for path in paths:
        try:
            doc = load_document(path, root)
            result = apply_document(doc) if args.apply else preview(doc)
        except Exception as exc:
            logger.exception("rechunk failed file=%s", path.name)
            result = {"status": "failed", "path": path.relative_to(root).as_posix(), "error": str(exc)}
            failed = True
        print(json.dumps(result, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

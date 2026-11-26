"""Preview by default; --apply publishes immutable revisions through the KB manifest.

Failed stages leave the active snapshot untouched. Old revisions are retained.
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
    from .indexer import publish_documents

    result = publish_documents([doc])
    return {"status": "applied", "path": doc["metadata"]["path"], **result, "removed_chunks": 0, "removed_parents": 0}


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

"""Build an isolated retrieval evaluation index without starting the full API server."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _resolve_path(value: str) -> Path:
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _collection_name(item: Any) -> str:
    return getattr(item, "name", None) or str(item)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an isolated Chroma index for retrieval benchmarking.")
    parser.add_argument("--chroma-path", required=True, help="Target isolated Chroma DB path")
    parser.add_argument("--ingest-path", required=True, help="Directory containing ingest docs")
    parser.add_argument(
        "--enable-parent-child",
        required=True,
        choices=["true", "false"],
        help="Whether to build the index with parent-child chunking enabled",
    )
    parser.add_argument("--output-json", default="", help="Optional JSON summary output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    root_dir = Path(__file__).resolve().parents[1]
    chroma_path = _resolve_path(args.chroma_path)
    ingest_path = _resolve_path(args.ingest_path)
    output_json = _resolve_path(args.output_json) if str(args.output_json).strip() else None

    load_dotenv(root_dir / ".env")
    os.environ["CHROMA_DB_PATH"] = str(chroma_path)
    os.environ["INGEST_PATH"] = str(ingest_path)
    os.environ["ENABLE_PARENT_CHILD_CHUNKING"] = args.enable_parent_child

    import chromadb

    from app.api.v1.startup_ingest import run_startup_ingestion_if_needed
    from software_recommend_system.config import settings as rag_settings

    chroma_path.parent.mkdir(parents=True, exist_ok=True)
    ingest_path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_path))
    collections_before = sorted(_collection_name(item) for item in client.list_collections())
    for name in collections_before:
        client.delete_collection(name)

    run_startup_ingestion_if_needed()

    refreshed_client = chromadb.PersistentClient(path=str(chroma_path))
    child_name = "software_recommendations"
    parent_name = str(getattr(rag_settings, "PARENT_COLLECTION_NAME", "software_recommendations_parent"))

    def collection_count(name: str) -> int:
        try:
            return int(refreshed_client.get_collection(name).count())
        except Exception:
            return 0

    summary = {
        "chroma_db_path": str(chroma_path),
        "ingest_path": str(ingest_path),
        "enable_parent_child_chunking": args.enable_parent_child == "true",
        "collections_before": collections_before,
        "collections_after": sorted(_collection_name(item) for item in refreshed_client.list_collections()),
        "child_collection_name": child_name,
        "parent_collection_name": parent_name,
        "child_collection_count": collection_count(child_name),
        "parent_collection_count": collection_count(parent_name),
    }

    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    if output_json is not None:
        print(f"OUTPUT_JSON={output_json}")


if __name__ == "__main__":
    main()

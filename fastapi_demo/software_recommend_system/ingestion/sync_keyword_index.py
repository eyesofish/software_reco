"""Repair the Elasticsearch mirror without re-reading files or re-embedding."""

import json
import logging

import chromadb

from ..config import settings
from ..keyword_index import get_keyword_index
from .indexer import DEFAULT_COLLECTION_NAME

logger = logging.getLogger(__name__)


def sync_keyword_index() -> dict[str, int]:
    # Do not create a missing collection: a misconfigured source must not wipe ES.
    collection = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH).get_collection(DEFAULT_COLLECTION_NAME)
    result = get_keyword_index().reconcile(collection)
    logger.info("keyword.index.sync.complete index=%s result=%s", settings.ELASTICSEARCH_INDEX, result)
    return result


def sync_keyword_index_on_startup() -> None:
    if not settings.RECALL_ENABLE_KEYWORD:
        return
    try:
        sync_keyword_index()
    except Exception:
        logger.exception("keyword.index.sync.failed; other retrieval channels remain available")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        result = sync_keyword_index()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()

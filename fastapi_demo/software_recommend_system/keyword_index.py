"""Elasticsearch full-text index, mirrored from the Chroma text collection.

Chroma remains the source of truth. Normal writes degrade on ES failure; a
strict, paginated reconciliation repairs missed writes without re-embedding.
"""

import logging
import re
import threading
import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from elasticsearch import BadRequestError, Elasticsearch, helpers

from .config import settings
from .document_schema import Document

logger = logging.getLogger(__name__)
BATCH_SIZE = 500
_WRITE_UNAVAILABLE_UNTIL = 0.0


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;/|]", value)
    if not isinstance(value, list):
        return []
    return [str(tag).strip() for tag in value if str(tag).strip()]


class KeywordIndex:
    def __init__(self, client: Any, index: str) -> None:
        self.client = client
        self.index = index
        # Serialize ES writes within this worker. Manual reconciliation must
        # run with ingestion paused; this is not a cross-process source lock.
        self.write_lock = threading.RLock()

    def ensure_index(self) -> None:
        if self.client.indices.exists(index=self.index):
            return
        try:
            self.client.indices.create(
                index=self.index,
                settings={"number_of_shards": 1, "number_of_replicas": 0},
                mappings={
                    "dynamic": "strict",
                    "properties": {
                        "content": {"type": "text", "analyzer": "cjk"},
                        "tags": {"type": "text", "analyzer": "cjk"},
                        "metadata": {"type": "object", "enabled": False},
                    },
                },
            )
        except BadRequestError as exc:
            # Another process may have initialized the index concurrently.
            if exc.error != "resource_already_exists_exception":
                raise

    def _bulk(self, actions: list[dict[str, Any]]) -> int:
        if not actions:
            return 0
        succeeded, errors = helpers.bulk(
            self.client,
            actions,
            chunk_size=BATCH_SIZE,
            raise_on_error=False,
            raise_on_exception=True,
            max_retries=0,
        )
        if errors:
            raise RuntimeError(f"ES bulk failed for {len(errors)} of {len(actions)} records")
        return int(succeeded)

    def upsert(
        self,
        ids: Sequence[str],
        texts: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> int:
        if not (len(ids) == len(texts) == len(metadatas)):
            raise ValueError("keyword ids, texts, and metadata length mismatch")
        with self.write_lock:
            self.ensure_index()
            return self._bulk(
                [
                    {
                        "_op_type": "index",
                        "_index": self.index,
                        "_id": str(chunk_id),
                        "_source": {
                            "content": str(text),
                            "tags": normalize_tags(metadata.get("tags")),
                            "metadata": dict(metadata),
                        },
                    }
                    for chunk_id, text, metadata in zip(ids, texts, metadatas, strict=True)
                ]
            )

    def delete(self, ids: Sequence[str]) -> int:
        with self.write_lock:
            # bulk deletion of an absent ID is idempotent (404).
            actions = [{"_op_type": "delete", "_index": self.index, "_id": str(i)} for i in ids]
            if not actions:
                return 0
            succeeded, errors = helpers.bulk(
                self.client,
                actions,
                chunk_size=BATCH_SIZE,
                ignore_status=(404,),
                raise_on_error=False,
                raise_on_exception=True,
                max_retries=0,
            )
            failures = [e for e in errors if e.get("delete", {}).get("status") != 404]
            if failures:
                raise RuntimeError(f"ES deletion failed for {len(failures)} records")
            return int(succeeded)

    def search(self, query: str, top_k: int) -> list[Document]:
        if not query.strip():
            return []
        response = self.client.search(
            index=self.index,
            size=max(1, int(top_k)),
            track_total_hits=False,
            allow_partial_search_results=False,
            query={"multi_match": {"query": query, "fields": ["content", "tags"], "type": "most_fields"}},
        )
        if response.get("timed_out"):
            raise TimeoutError("Elasticsearch keyword search timed out")
        documents = []
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            metadata = dict(source.get("metadata") or {})
            metadata["source"] = metadata.get("source") or "keyword_index"
            metadata["doc_id"] = (
                metadata.get("source_doc_id") or metadata.get("doc_id") or metadata.get("filename") or hit["_id"]
            )
            metadata["tags"] = normalize_tags(metadata.get("tags"))
            documents.append(Document(content=source["content"], metadata=metadata, score=float(hit["_score"] or 0)))
        return documents

    def reconcile(self, collection: Any) -> dict[str, int]:
        """Read every source page; only prune after a complete successful copy."""
        with self.write_lock:
            self.ensure_index()
            expected = collection.count()
            seen: set[str] = set()
            indexed = 0
            for offset in range(0, expected, BATCH_SIZE):
                page = collection.get(limit=BATCH_SIZE, offset=offset, include=["documents", "metadatas"])
                ids = page["ids"]
                if not ids or len(ids) != len(set(ids)) or seen.intersection(ids):
                    raise RuntimeError("Incomplete or overlapping Chroma pages; ES pruning skipped")
                texts = page.get("documents") or []
                metadatas = [dict(m or {}) for m in (page.get("metadatas") or [])]
                if any(text is None for text in texts):
                    raise RuntimeError("Missing Chroma document text; ES pruning skipped")
                indexed += self.upsert(ids, texts, metadatas)
                seen.update(ids)
            if len(seen) != expected or collection.count() != expected:
                raise RuntimeError("Chroma changed during synchronization; ES pruning skipped")
            self.client.indices.refresh(index=self.index)
            # Finish enumeration before deleting anything: scan failure must not
            # leave a partially pruned index.
            stale = [
                hit["_id"]
                for hit in helpers.scan(
                    self.client,
                    index=self.index,
                    query={"query": {"match_all": {}}, "_source": False},
                    size=BATCH_SIZE,
                )
                if hit["_id"] not in seen
            ]
            deleted = self.delete(stale)
            self.client.indices.refresh(index=self.index)
            return {"indexed": indexed, "deleted": deleted, "failed": 0}


@lru_cache(maxsize=8)
def _cached_index(url: str, index: str, timeout: float) -> KeywordIndex:
    return KeywordIndex(Elasticsearch(url, request_timeout=timeout, max_retries=0), index)


def get_keyword_index() -> KeywordIndex:
    return _cached_index(
        settings.ELASTICSEARCH_URL,
        settings.ELASTICSEARCH_INDEX,
        settings.ELASTICSEARCH_REQUEST_TIMEOUT_SECONDS,
    )


def mirror_chunks(ids: Sequence[str], texts: Sequence[str], metadatas: Sequence[dict[str, Any]]) -> None:
    _mirror("upsert", ids, texts, metadatas)


def mirror_deletions(ids: Sequence[str]) -> None:
    _mirror("delete", ids)


def _mirror(operation: str, ids: Sequence[str], *args: Any) -> None:
    global _WRITE_UNAVAILABLE_UNTIL
    if not settings.RECALL_ENABLE_KEYWORD or not ids:
        return
    if time.monotonic() < _WRITE_UNAVAILABLE_UNTIL:
        logger.warning("keyword.index.skipped operation=%s records=%s reason=circuit_open", operation, len(ids))
        return
    try:
        getattr(get_keyword_index(), operation)(ids, *args)
    except Exception as exc:
        _WRITE_UNAVAILABLE_UNTIL = time.monotonic() + settings.KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS
        logger.warning("keyword.index.failed operation=%s records=%s error=%s", operation, len(ids), exc)

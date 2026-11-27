import logging
from typing import Any

logger = logging.getLogger(__name__)


def initialize_vector_store(documents: list[dict[str, Any]]) -> bool:
    """Publish a complete immutable revision set; failures retain the active snapshot."""
    from .ingestion.indexer import publish_documents

    try:
        result = publish_documents(documents)
        logger.info(
            "Ingested documents snapshot_id=%s documents=%s chunks=%s",
            result["snapshot_id"],
            result["documents"],
            result["chunks"],
        )
        return True
    except Exception as exc:
        logger.error("Failed to publish vector store: %s", str(exc))
        return False

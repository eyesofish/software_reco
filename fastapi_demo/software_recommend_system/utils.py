from typing import Any, Dict, List
import logging

from .config import settings
from .ingestion.loader import normalize_documents
from .ingestion.chunker import chunk_documents
from .ingestion.embedder import embed_texts
from .ingestion.indexer import index_embeddings

logger = logging.getLogger(__name__)


def initialize_vector_store(documents: List[Dict[str, Any]]) -> bool:
    """Ingest raw docs into Chroma through loader -> chunker -> embedder -> indexer."""
    try:
        normalized_documents = normalize_documents(documents)
        if not normalized_documents:
            logger.warning("No valid documents to ingest")
            return False

        chunks = chunk_documents(
            normalized_documents,
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        )
        if not chunks:
            logger.warning("Chunking produced no content")
            return False

        chunk_ids = [str(chunk["id"]) for chunk in chunks]
        chunk_texts = [str(chunk["content"]) for chunk in chunks]
        metadatas = [dict(chunk.get("metadata", {})) for chunk in chunks]

        embeddings = embed_texts(chunk_texts)
        indexed_count = index_embeddings(
            chunk_ids=chunk_ids,
            chunk_texts=chunk_texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

        logger.info(
            "Ingested %s documents into %s chunks (%s indexed vectors)",
            len(normalized_documents),
            len(chunks),
            indexed_count,
        )
        return True
    except Exception as exc:
        logger.error("Failed to initialize vector store: %s", str(exc))
        return False

import logging
from typing import Any

from .config import settings
from .ingestion.chunker import chunk_documents, chunk_documents_parent_child
from .ingestion.embedder import embed_texts
from .ingestion.indexer import index_embeddings, index_parent_documents
from .ingestion.loader import normalize_documents

logger = logging.getLogger(__name__)


def initialize_vector_store(documents: list[dict[str, Any]]) -> bool:
    """Ingest raw docs into Chroma through loader -> chunker -> embedder -> indexer."""
    try:
        normalized_documents = normalize_documents(documents)
        if not normalized_documents:
            logger.warning("No valid documents to ingest")
            return False

        if settings.ENABLE_PARENT_CHILD_CHUNKING:
            parent_chunks, child_chunks = chunk_documents_parent_child(
                normalized_documents,
                parent_chunk_size=settings.PARENT_CHUNK_SIZE,
                parent_chunk_overlap=settings.PARENT_CHUNK_OVERLAP,
                child_chunk_size=settings.CHILD_CHUNK_SIZE,
                child_chunk_overlap=settings.CHILD_CHUNK_OVERLAP,
            )
            if not parent_chunks or not child_chunks:
                logger.warning("Parent-child chunking produced no content")
                return False

            parent_ids = [str(chunk["id"]) for chunk in parent_chunks]
            parent_texts = [str(chunk["content"]) for chunk in parent_chunks]
            parent_metadatas = [dict(chunk.get("metadata", {})) for chunk in parent_chunks]
            indexed_parents = index_parent_documents(
                parent_ids=parent_ids,
                parent_texts=parent_texts,
                metadatas=parent_metadatas,
                collection_name=settings.PARENT_COLLECTION_NAME,
            )

            child_ids = [str(chunk["id"]) for chunk in child_chunks]
            child_texts = [str(chunk["content"]) for chunk in child_chunks]
            child_metadatas = [dict(chunk.get("metadata", {})) for chunk in child_chunks]
            child_embeddings = embed_texts(child_texts)
            indexed_children = index_embeddings(
                chunk_ids=child_ids,
                chunk_texts=child_texts,
                metadatas=child_metadatas,
                embeddings=child_embeddings,
            )

            logger.info(
                "Ingested %s documents into %s parent chunks and %s child chunks (%s parent records, %s child vectors)",
                len(normalized_documents),
                len(parent_chunks),
                len(child_chunks),
                indexed_parents,
                indexed_children,
            )
            return True

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

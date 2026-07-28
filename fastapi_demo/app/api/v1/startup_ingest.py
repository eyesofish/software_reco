import logging
import re
from pathlib import Path

from app.core.config import settings
from software_recommend_system.config import settings as rag_settings
from software_recommend_system.ingestion.chunker import chunk_documents, chunk_documents_parent_child
from software_recommend_system.ingestion.embedder import embed_texts
from software_recommend_system.ingestion.indexer import (
    get_chroma_collection,
    index_embeddings,
    index_parent_documents,
)
from software_recommend_system.ingestion.loader import normalize_documents
from software_recommend_system.multimodal import (
    IMAGE_MEDIA_TYPE_BY_SUFFIX,
    MultimodalInputError,
    VisionProcessingError,
    describe_local_image,
)

logger = logging.getLogger(__name__)

SUPPORTED_TEXT_EXTENSIONS = (".txt", ".md", ".pdf")
SUPPORTED_IMAGE_EXTENSIONS = tuple(sorted(IMAGE_MEDIA_TYPE_BY_SUFFIX))
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS + SUPPORTED_IMAGE_EXTENSIONS


def _safe_doc_id(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return re.sub(r"[^A-Za-z0-9._/-]", "_", normalized)


def _read_plain_text(file_path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding).strip()
        except Exception:
            continue
    logger.warning("failed to decode text file with supported encodings: %s", file_path)
    return ""


def _read_pdf_text(file_path: Path) -> str:
    try:
        from PyPDF2 import PdfReader
    except Exception:
        logger.warning("PyPDF2 not installed, skipping pdf file: %s", file_path)
        return ""

    try:
        reader = PdfReader(str(file_path))
        parts: list[str] = []
        for page_index, page in enumerate(reader.pages):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                parts.append(page_text)
            else:
                logger.debug("empty text for pdf page file=%s page=%s", file_path, page_index)
        return "\n".join(parts).strip()
    except Exception as exc:
        logger.warning("failed to read pdf file=%s error=%s", file_path, exc)
        return ""


def _read_file_content(file_path: Path) -> tuple[str, dict[str, object]]:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf_text(file_path), {"modality": "text"}
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        image = describe_local_image(file_path)
        description = str(image["description"] or "").strip()
        content = (
            f"Image knowledge asset: {file_path.name}\n"
            f"Visual description: {description}"
        )
        return content, {
            "modality": "image",
            "media_type": image["media_type"],
            "asset_path": "",
        }
    return _read_plain_text(file_path), {"modality": "text"}


def _list_candidate_files(root_path: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in root_path.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    )


def _build_raw_document(
    file_path: Path,
    ingest_root: Path,
    content: str,
    used_doc_ids: set[str],
    extra_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    relative = file_path.relative_to(ingest_root).as_posix()
    is_image = file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    preferred_doc_id = _safe_doc_id(relative if is_image else file_path.name)
    doc_id = preferred_doc_id or _safe_doc_id(relative) or f"doc_{len(used_doc_ids) + 1}"
    if doc_id in used_doc_ids:
        base_doc_id = doc_id
        suffix = 2
        while f"{base_doc_id}__{suffix}" in used_doc_ids:
            suffix += 1
        doc_id = f"{base_doc_id}__{suffix}"
        logger.warning(
            "startup ingest doc_id collision: file=%s base_doc_id=%s resolved_doc_id=%s",
            file_path,
            base_doc_id,
            doc_id,
        )
    used_doc_ids.add(doc_id)
    metadata: dict[str, object] = {
        "source": "startup_ingest",
        "path": relative,
        "filename": file_path.name,
        "extension": file_path.suffix.lower(),
        **dict(extra_metadata or {}),
    }
    if is_image:
        metadata["asset_path"] = relative
    return {
        "id": doc_id,
        "content": content,
        "metadata": metadata,
    }


def _ingest_single_document(raw_doc: dict[str, object]) -> int:
    normalized_docs = normalize_documents([raw_doc])
    if not normalized_docs:
        return 0

    if rag_settings.ENABLE_PARENT_CHILD_CHUNKING:
        parent_chunks, child_chunks = chunk_documents_parent_child(
            normalized_docs,
            parent_chunk_size=rag_settings.PARENT_CHUNK_SIZE,
            parent_chunk_overlap=rag_settings.PARENT_CHUNK_OVERLAP,
            child_chunk_size=rag_settings.CHILD_CHUNK_SIZE,
            child_chunk_overlap=rag_settings.CHILD_CHUNK_OVERLAP,
        )
        if not parent_chunks or not child_chunks:
            return 0

        parent_ids = [str(chunk["id"]) for chunk in parent_chunks]
        parent_texts = [str(chunk["content"]) for chunk in parent_chunks]
        parent_metadatas = [dict(chunk.get("metadata", {})) for chunk in parent_chunks]

        indexed_parents = index_parent_documents(
            parent_ids=parent_ids,
            parent_texts=parent_texts,
            metadatas=parent_metadatas,
            collection_name=rag_settings.PARENT_COLLECTION_NAME,
        )

        child_ids = [str(chunk["id"]) for chunk in child_chunks]
        child_texts = [str(chunk["content"]) for chunk in child_chunks]
        child_metadatas = [dict(chunk.get("metadata", {})) for chunk in child_chunks]

        logger.info(
            "startup ingest parent-child chunks generated: doc_id=%s parents=%s children=%s",
            normalized_docs[0]["id"],
            len(parent_chunks),
            len(child_chunks),
        )

        child_embeddings = embed_texts(child_texts)
        indexed_children = index_embeddings(
            chunk_ids=child_ids,
            chunk_texts=child_texts,
            metadatas=child_metadatas,
            embeddings=child_embeddings,
        )
        logger.info(
            "startup ingest parent-child indexed: doc_id=%s parent_vectors=%s child_vectors=%s",
            normalized_docs[0]["id"],
            indexed_parents,
            indexed_children,
        )
        return indexed_children

    chunks = chunk_documents(
        normalized_docs,
        chunk_size=rag_settings.CHUNK_SIZE,
        chunk_overlap=rag_settings.CHUNK_OVERLAP,
    )
    if not chunks:
        return 0

    chunk_ids = [str(chunk["id"]) for chunk in chunks]
    chunk_texts = [str(chunk["content"]) for chunk in chunks]
    metadatas = [dict(chunk.get("metadata", {})) for chunk in chunks]

    logger.info(
        "startup ingest chunks generated: doc_id=%s chunks=%s",
        normalized_docs[0]["id"],
        len(chunks),
    )

    embeddings = embed_texts(chunk_texts)
    indexed_count = index_embeddings(
        chunk_ids=chunk_ids,
        chunk_texts=chunk_texts,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    return indexed_count


def _is_source_path_indexed(collection, relative_path: str) -> bool:
    try:
        result = collection.get(where={"path": relative_path}, limit=1)
    except Exception as exc:
        logger.warning(
            "startup ingest path lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return False
    return bool((result or {}).get("ids"))


def run_startup_ingestion_if_needed() -> None:
    """Incrementally ingest supported text and image files from INGEST_PATH."""
    ingest_root = Path(settings.INGEST_PATH).expanduser()
    logger.info("startup ingest begin: ingest_path=%s", ingest_root)

    collection = get_chroma_collection()
    current_count = collection.count()
    logger.info("startup ingest vector count before run=%s", current_count)
    if not ingest_root.exists() or not ingest_root.is_dir():
        logger.info("startup ingest skipped: ingest path not found or not a directory")
        logger.info(
            "startup.ingest.complete status=skipped reason=ingest_path_missing processed_files=0 added_vectors=0"
        )
        return

    files = _list_candidate_files(ingest_root)
    logger.info("startup ingest files found=%s", len(files))
    if not files:
        logger.info("startup ingest skipped: no supported files")
        logger.info(
            "startup.ingest.complete status=skipped reason=no_supported_files processed_files=0 added_vectors=0"
        )
        return

    total_files = 0
    total_vectors = 0
    skipped_files = 0
    used_doc_ids: set[str] = set()

    for file_path in files:
        relative = file_path.relative_to(ingest_root).as_posix()
        if _is_source_path_indexed(collection, relative):
            skipped_files += 1
            logger.info("startup ingest skip indexed file: %s", file_path)
            continue

        try:
            content, extra_metadata = _read_file_content(file_path)
        except (MultimodalInputError, VisionProcessingError) as exc:
            logger.warning("startup ingest skip image file=%s error=%s", file_path, exc)
            continue
        if not content:
            logger.info("startup ingest skip empty file: %s", file_path)
            continue

        raw_doc = _build_raw_document(
            file_path=file_path,
            ingest_root=ingest_root,
            content=content,
            used_doc_ids=used_doc_ids,
            extra_metadata=extra_metadata,
        )
        logger.info("startup ingest processing file=%s", file_path)

        try:
            indexed_count = _ingest_single_document(raw_doc)
            total_files += 1
            total_vectors += indexed_count
            logger.info("startup ingest vectors added file=%s count=%s", file_path, indexed_count)
        except Exception as exc:
            logger.exception("startup ingest failed for file=%s error=%s", file_path, exc)

    logger.info(
        "startup ingest complete: processed_files=%s skipped_files=%s added_vectors=%s",
        total_files,
        skipped_files,
        total_vectors,
    )
    logger.info(
        "startup.ingest.complete status=ok processed_files=%s skipped_files=%s added_vectors=%s vector_count_before=%s",
        total_files,
        skipped_files,
        total_vectors,
        current_count,
    )

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from software_recommend_system.config import settings as rag_settings
from software_recommend_system.image_embedder import (
    ImageEmbeddingError,
    embed_image_files,
    image_embedding_identity,
)
from software_recommend_system.ingestion.chunker import chunk_documents, chunk_documents_parent_child
from software_recommend_system.ingestion.embedder import embed_texts
from software_recommend_system.ingestion.indexer import (
    get_chroma_collection,
    get_image_collection,
    get_parent_collection,
    index_embeddings,
    index_image_embeddings,
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
            "source_sha256": _image_file_sha256(file_path),
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


def _source_path_ids(collection: Any, relative_path: str) -> list[str]:
    try:
        result = collection.get(where={"path": relative_path})
    except Exception as exc:
        logger.warning(
            "startup ingest source id lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return []
    raw_ids = list((result or {}).get("ids") or [])
    if raw_ids and isinstance(raw_ids[0], list):
        raw_ids = list(raw_ids[0])
    return [str(item) for item in raw_ids if str(item).strip()]


def _delete_stale_source_ids(
    collection: Any,
    relative_path: str,
    keep_ids: list[str],
) -> None:
    keep = set(keep_ids)
    stale_ids = [
        item
        for item in _source_path_ids(collection, relative_path)
        if item not in keep
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)


def _ingest_single_document(
    raw_doc: dict[str, object],
    *,
    replace_source_path: str | None = None,
) -> int:
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
        indexed_parents = index_parent_documents(
            parent_ids=parent_ids,
            parent_texts=parent_texts,
            metadatas=parent_metadatas,
            collection_name=rag_settings.PARENT_COLLECTION_NAME,
        )
        indexed_children = index_embeddings(
            chunk_ids=child_ids,
            chunk_texts=child_texts,
            metadatas=child_metadatas,
            embeddings=child_embeddings,
        )
        if replace_source_path:
            _delete_stale_source_ids(
                get_chroma_collection(),
                replace_source_path,
                child_ids,
            )
            _delete_stale_source_ids(
                get_parent_collection(rag_settings.PARENT_COLLECTION_NAME),
                replace_source_path,
                parent_ids,
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
    if replace_source_path:
        _delete_stale_source_ids(
            get_chroma_collection(),
            replace_source_path,
            chunk_ids,
        )
        parent_collection = get_parent_collection(rag_settings.PARENT_COLLECTION_NAME)
        stale_parent_ids = _source_path_ids(parent_collection, replace_source_path)
        if stale_parent_ids:
            parent_collection.delete(ids=stale_parent_ids)
    return indexed_count


def _read_indexed_content(collection: Any, relative_path: str) -> str:
    try:
        result = collection.get(
            where={"path": relative_path},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        logger.warning(
            "startup ingest existing content lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return ""

    documents = list((result or {}).get("documents") or [])
    if documents and isinstance(documents[0], list):
        documents = list(documents[0])
    metadatas = list((result or {}).get("metadatas") or [])
    if metadatas and isinstance(metadatas[0], list):
        metadatas = list(metadatas[0])

    rows: list[tuple[int | None, int | None, int, str]] = []
    for index, raw_content in enumerate(documents):
        content = str(raw_content or "")
        if not content:
            continue
        metadata = metadatas[index] if index < len(metadatas) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            chunk_start = int(metadata["chunk_start"])
            chunk_end = int(metadata["chunk_end"])
        except (KeyError, TypeError, ValueError):
            chunk_start = None
            chunk_end = None
        rows.append((chunk_start, chunk_end, index, content))

    rows.sort(
        key=lambda row: (
            row[0] is None,
            row[0] if row[0] is not None else row[2],
            row[2],
        )
    )
    assembled = ""
    assembled_end = 0
    for chunk_start, chunk_end, _, content in rows:
        if not assembled:
            assembled = content
            assembled_end = chunk_end if chunk_end is not None else len(content)
            continue
        if chunk_start is None or chunk_end is None:
            if content not in assembled:
                assembled += f"\n{content}"
            continue

        overlap = max(0, assembled_end - chunk_start)
        if overlap < len(content):
            separator = "" if overlap else "\n"
            assembled += f"{separator}{content[overlap:]}"
        assembled_end = max(assembled_end, chunk_end)
    return assembled.strip()


def _read_indexed_image_record(
    collection: Any,
    relative_path: str,
) -> tuple[str, dict[str, object]] | None:
    try:
        result = collection.get(
            where={"path": relative_path},
            include=["documents", "metadatas"],
            limit=1,
        )
    except Exception as exc:
        logger.warning(
            "startup ingest existing image lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return None

    documents = list((result or {}).get("documents") or [])
    metadatas = list((result or {}).get("metadatas") or [])
    if documents and isinstance(documents[0], list):
        documents = list(documents[0])
    if metadatas and isinstance(metadatas[0], list):
        metadatas = list(metadatas[0])
    if not documents:
        return None
    metadata = metadatas[0] if metadatas and isinstance(metadatas[0], dict) else {}
    return str(documents[0] or "").strip(), dict(metadata)


def _read_indexed_source_sha256(collection: Any, relative_path: str) -> str:
    try:
        result = collection.get(
            where={"path": relative_path},
            include=["metadatas"],
            limit=1,
        )
    except Exception as exc:
        logger.warning(
            "startup ingest source fingerprint lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return ""
    metadatas = list((result or {}).get("metadatas") or [])
    if metadatas and isinstance(metadatas[0], list):
        metadatas = list(metadatas[0])
    metadata = metadatas[0] if metadatas and isinstance(metadatas[0], dict) else {}
    return str(metadata.get("source_sha256", "") or "").strip()


def _image_record_is_current(
    record: tuple[str, dict[str, object]],
    *,
    content: str,
    relative_path: str,
    file_path: Path,
) -> bool:
    stored_content, metadata = record
    try:
        source_sha256 = _image_file_sha256(file_path)
    except ImageEmbeddingError:
        return False
    expected = {
        "path": relative_path,
        "filename": file_path.name,
        "media_type": IMAGE_MEDIA_TYPE_BY_SUFFIX.get(file_path.suffix.lower(), ""),
        "modality": "image",
        "asset_path": relative_path,
        "image_embedding_model": image_embedding_identity(),
        "source_sha256": source_sha256,
    }
    if stored_content.strip() != str(content or "").strip():
        return False
    return all(str(metadata.get(key, "") or "") == str(value) for key, value in expected.items())


def _image_file_sha256(file_path: Path) -> str:
    max_image_bytes = max(1, int(rag_settings.MULTIMODAL_MAX_IMAGE_BYTES))
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise ImageEmbeddingError(f"Failed to stat image {file_path}: {exc}") from exc
    if file_size > max_image_bytes:
        raise ImageEmbeddingError(
            f"Image {file_path.name} exceeds the {max_image_bytes}-byte embedding limit"
        )

    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ImageEmbeddingError(f"Failed to read image {file_path}: {exc}") from exc
    return digest.hexdigest()


def _image_extra_metadata(file_path: Path) -> dict[str, object]:
    media_type = IMAGE_MEDIA_TYPE_BY_SUFFIX.get(file_path.suffix.lower(), "")
    return {
        "modality": "image",
        "media_type": media_type,
        "asset_path": "",
    }


def _fallback_image_content(file_path: Path) -> str:
    return f"Image knowledge asset: {file_path.name}"


def _index_image_document(file_path: Path, raw_doc: dict[str, object]) -> int:
    doc_id = str(raw_doc.get("id", "") or "").strip()
    content = str(raw_doc.get("content", "") or "").strip()
    raw_metadata = raw_doc.get("metadata", {})
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata.update(
        {
            "doc_id": doc_id,
            "source_doc_id": doc_id,
            "image_embedding_model": image_embedding_identity(),
            "source_sha256": _image_file_sha256(file_path),
        }
    )
    metadata = {
        key: value
        for key, value in metadata.items()
        if value is not None and isinstance(value, (str, int, float, bool))
    }
    embeddings = embed_image_files([file_path])
    return index_image_embeddings(
        image_ids=[doc_id],
        captions=[content],
        metadatas=[metadata],
        embeddings=embeddings,
    )


def _is_source_path_indexed(collection: Any, relative_path: str) -> bool:
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
    image_vector_enabled = bool(rag_settings.RECALL_ENABLE_IMAGE_VECTOR)
    image_collection = None
    if image_vector_enabled:
        try:
            image_collection = get_image_collection()
        except Exception as exc:
            image_vector_enabled = False
            logger.exception(
                "startup ingest image collection unavailable; native image indexing disabled: %s",
                exc,
            )
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
    total_text_vectors = 0
    total_image_vectors = 0
    skipped_files = 0
    used_doc_ids: set[str] = set()

    for file_path in files:
        relative = file_path.relative_to(ingest_root).as_posix()
        is_image = file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        text_indexed = _is_source_path_indexed(collection, relative)
        text_needs_refresh = False
        source_sha256 = ""
        if is_image:
            try:
                source_sha256 = _image_file_sha256(file_path)
            except ImageEmbeddingError as exc:
                logger.warning(
                    "startup ingest image fingerprint unavailable file=%s error=%s",
                    file_path,
                    exc,
                )
            if text_indexed and source_sha256:
                indexed_source_sha256 = _read_indexed_source_sha256(
                    collection,
                    relative,
                )
                text_needs_refresh = (
                    not indexed_source_sha256
                    or indexed_source_sha256 != source_sha256
                )
        indexed_content = ""
        image_record: tuple[str, dict[str, object]] | None = None
        image_indexed = True
        if is_image and image_vector_enabled and image_collection is not None:
            image_record = _read_indexed_image_record(image_collection, relative)
            image_indexed = image_record is not None
            if text_indexed:
                indexed_content = _read_indexed_content(collection, relative)
            if image_record is not None:
                current_content = indexed_content or image_record[0]
                image_indexed = _image_record_is_current(
                    image_record,
                    content=current_content,
                    relative_path=relative,
                    file_path=file_path,
                )

        if text_indexed and not text_needs_refresh and image_indexed:
            skipped_files += 1
            logger.info("startup ingest skip indexed file: %s", file_path)
            continue

        content = ""
        extra_metadata: dict[str, object] = {}
        can_index_text = False
        if not text_indexed or text_needs_refresh:
            try:
                content, extra_metadata = _read_file_content(file_path)
                can_index_text = bool(content)
            except (
                ImageEmbeddingError,
                MultimodalInputError,
                VisionProcessingError,
            ) as exc:
                logger.warning(
                    "startup ingest caption route failed file=%s error=%s",
                    file_path,
                    exc,
                )

        if is_image and not extra_metadata:
            extra_metadata = _image_extra_metadata(file_path)
        if not content and indexed_content:
            content = indexed_content
        native_refresh_blocked = bool(
            image_record is not None
            and
            (not text_indexed or text_needs_refresh)
            and not can_index_text
        )
        if (
            native_refresh_blocked
            and image_record is not None
            and image_collection is not None
        ):
            try:
                image_collection.delete(where={"path": relative})
                image_indexed = True
                logger.warning(
                    "startup ingest removed stale native image row after caption refresh failure: %s",
                    file_path,
                )
            except Exception as exc:
                logger.warning(
                    "startup ingest failed to remove stale native image row file=%s error=%s",
                    file_path,
                    exc,
                )
        if (
            is_image
            and image_record is not None
            and content
            and not _image_record_is_current(
                image_record,
                content=content,
                relative_path=relative,
                file_path=file_path,
            )
        ):
            image_indexed = False
        if not content and is_image and image_vector_enabled and not image_indexed:
            content = _fallback_image_content(file_path)
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

        file_processed = False
        if (not text_indexed or text_needs_refresh) and can_index_text:
            try:
                indexed_count = _ingest_single_document(
                    raw_doc,
                    replace_source_path=relative if text_needs_refresh else None,
                )
                total_text_vectors += indexed_count
                file_processed = file_processed or indexed_count > 0
                logger.info(
                    "startup ingest text vectors added file=%s count=%s",
                    file_path,
                    indexed_count,
                )
            except Exception as exc:
                logger.exception("startup ingest text indexing failed for file=%s error=%s", file_path, exc)

        if (
            is_image
            and image_vector_enabled
            and not image_indexed
            and not native_refresh_blocked
        ):
            try:
                indexed_image_count = _index_image_document(file_path, raw_doc)
                total_image_vectors += indexed_image_count
                file_processed = file_processed or indexed_image_count > 0
                logger.info(
                    "startup ingest image vectors added file=%s count=%s",
                    file_path,
                    indexed_image_count,
                )
            except ImageEmbeddingError as exc:
                logger.warning(
                    "startup ingest native image indexing unavailable file=%s error=%s",
                    file_path,
                    exc,
                )
            except Exception as exc:
                logger.exception(
                    "startup ingest native image indexing failed file=%s error=%s",
                    file_path,
                    exc,
                )

        if file_processed:
            total_files += 1

    total_vectors = total_text_vectors + total_image_vectors
    logger.info(
        "startup ingest complete: processed_files=%s skipped_files=%s text_vectors=%s image_vectors=%s",
        total_files,
        skipped_files,
        total_text_vectors,
        total_image_vectors,
    )
    logger.info(
        "startup.ingest.complete status=ok processed_files=%s skipped_files=%s "
        "added_vectors=%s text_vectors=%s image_vectors=%s vector_count_before=%s",
        total_files,
        skipped_files,
        total_vectors,
        total_text_vectors,
        total_image_vectors,
        current_count,
    )

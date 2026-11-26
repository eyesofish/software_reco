import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from software_recommend_system.config import settings as rag_settings
from software_recommend_system.image_embedder import (
    ImageEmbeddingError,
    image_embedding_identity,
)
from software_recommend_system.ingestion.indexer import (
    get_chroma_collection,
)
from software_recommend_system.multimodal import (
    IMAGE_MEDIA_TYPE_BY_SUFFIX,
    describe_local_image,
)

logger = logging.getLogger(__name__)

SUPPORTED_TEXT_EXTENSIONS = (".txt", ".md", ".pdf")
SUPPORTED_IMAGE_EXTENSIONS = tuple(sorted(IMAGE_MEDIA_TYPE_BY_SUFFIX))
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS + SUPPORTED_IMAGE_EXTENSIONS


def _safe_doc_id(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return re.sub(r"[^A-Za-z0-9._/-]", "_", normalized)


def _stable_text_doc_id(relative_path: str) -> str:
    safe = _safe_doc_id(relative_path)
    if safe != relative_path:
        safe += "-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]
    return safe


def _read_plain_text(file_path: Path) -> str:
    encodings = ("utf-8-sig", "utf-8", "gb18030")
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding).strip()
        except Exception:
            continue
    logger.warning("failed to decode text file with supported encodings: %s", file_path)
    return ""


def _read_pdf_text(file_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        logger.warning("pypdf not installed, skipping pdf file: %s", file_path)
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
        if rag_settings.CHUNKING_STRATEGY == "structure":
            from software_recommend_system.ingestion.structure import parse_pdf

            try:
                content, blocks, warnings = parse_pdf(file_path)
            except Exception as exc:
                raise ValueError(f"Failed to parse PDF {file_path.name}: {type(exc).__name__}") from exc
            return content, {"modality": "text", "_structure_blocks": blocks, "parse_warnings": ";".join(warnings)}
        return _read_pdf_text(file_path), {"modality": "text"}
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        image = describe_local_image(file_path)
        description = str(image["description"] or "").strip()
        content = f"Image knowledge asset: {file_path.name}\nVisual description: {description}"
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
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and ".versions" not in path.relative_to(root_path).parts
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
    preferred_doc_id = _stable_text_doc_id(relative)
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
    blocks = metadata.pop("_structure_blocks", None)
    return {
        "id": doc_id,
        "content": content,
        "metadata": metadata,
        **({"blocks": blocks} if blocks is not None else {}),
    }


def _source_path_ids(collection: Any, relative_path: str) -> list[str]:
    try:
        from software_recommend_system.kb_versions import chroma_filter

        membership = chroma_filter()
        where = {"$and": [{"path": relative_path}, membership]} if membership else {"path": relative_path}
        result = collection.get(where=where)
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


def _ingest_single_document(
    raw_doc: dict[str, object],
    *,
    replace_source_path: str | None = None,
) -> int:
    """Publish all channels for one source; retain older immutable revisions."""
    from software_recommend_system.ingestion.indexer import publish_documents

    metadata = dict(raw_doc.get("metadata") or {})
    images = {}
    if metadata.get("modality") == "image":
        images[str(raw_doc["id"])] = Path(settings.INGEST_PATH) / str(metadata["path"])
    result = publish_documents([raw_doc], image_sources=images, ingest_root=settings.INGEST_PATH)
    return int(result["chunks"])


def _read_indexed_content(collection: Any, relative_path: str) -> str:
    try:
        result = collection.get(
            where=_source_filter(relative_path),
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
        # Context prefixes (section names, repeated table headers/fences) are not source text.
        if "content_body_start" in metadata and "content_body_end" in metadata:
            content = content[int(metadata["content_body_start"]) : int(metadata["content_body_end"])]
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
            where=_source_filter(relative_path),
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
            where=_source_filter(relative_path),
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
        raise ImageEmbeddingError(f"Image {file_path.name} exceeds the {max_image_bytes}-byte embedding limit")

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
    """Compatibility entrypoint: publish caption, asset, and native vector together."""
    from software_recommend_system.ingestion.indexer import publish_documents

    return int(
        publish_documents([raw_doc], image_sources={str(raw_doc["id"]): file_path}, ingest_root=settings.INGEST_PATH)[
            "images"
        ]
    )


def _is_source_path_indexed(collection: Any, relative_path: str) -> bool:
    try:
        from software_recommend_system.kb_versions import chroma_filter

        membership = chroma_filter()
        where = {"$and": [{"path": relative_path}, membership]} if membership else {"path": relative_path}
        result = collection.get(where=where, limit=1)
    except Exception as exc:
        logger.warning(
            "startup ingest path lookup failed path=%s error=%s",
            relative_path,
            exc,
        )
        return False
    return bool((result or {}).get("ids"))


def run_startup_ingestion_if_needed() -> None:
    """Publish changed sources without removing earlier snapshot rows or assets."""
    from software_recommend_system.kb_versions import capture_snapshot, use_snapshot

    root = Path(settings.INGEST_PATH).expanduser()
    if not root.is_dir():
        return
    used_doc_ids: set[str] = set()
    processed = 0
    for file_path in _list_candidate_files(root):
        relative = file_path.relative_to(root).as_posix()
        try:
            before = hashlib.sha256(file_path.read_bytes()).hexdigest()
            # Captions can be reused only when they describe the exact same bytes.
            content = ""
            metadata: dict[str, object] = {}
            is_image = file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            if is_image:
                with use_snapshot(capture_snapshot()):
                    collection = get_chroma_collection()
                    if _read_indexed_source_sha256(collection, relative) == before:
                        content = _read_indexed_content(collection, relative)
                if content:
                    metadata = {**_image_extra_metadata(file_path), "source_sha256": before}
            if not content:
                content, metadata = _read_file_content(file_path)
            if not content.strip():
                raise ValueError("No extractable content; current revision retained")
            if hashlib.sha256(file_path.read_bytes()).hexdigest() != before:
                raise ValueError("Source changed while parsing; current revision retained")
            metadata["source_sha256"] = before
            doc = _build_raw_document(file_path, root, content, used_doc_ids, metadata)
            processed += int(_ingest_single_document(doc, replace_source_path=relative) > 0)
        except Exception as exc:
            logger.exception(
                "startup ingest publication failed path=%s error=%s; previous snapshot retained", relative, exc
            )
    logger.info("startup.ingest.complete processed_files=%s snapshot_id=%s", processed, capture_snapshot())


def _source_filter(relative_path: str) -> dict[str, Any]:
    from software_recommend_system.kb_versions import chroma_filter

    membership = chroma_filter()
    return {"$and": [{"path": relative_path}, membership]} if membership is not None else {"path": relative_path}

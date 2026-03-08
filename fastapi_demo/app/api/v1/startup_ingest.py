import logging
import re
from pathlib import Path
from typing import Dict, List, Set

from app.core.config import settings
from software_recommend_system.config import settings as rag_settings
from software_recommend_system.ingestion.chunker import chunk_documents
from software_recommend_system.ingestion.embedder import embed_texts
from software_recommend_system.ingestion.indexer import get_chroma_collection, index_embeddings
from software_recommend_system.ingestion.loader import normalize_documents

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")


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
        parts: List[str] = []
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


def _read_file_content(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf_text(file_path)
    return _read_plain_text(file_path)


def _list_candidate_files(root_path: Path) -> List[Path]:
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
    used_doc_ids: Set[str],
) -> Dict[str, object]:
    relative = file_path.relative_to(ingest_root).as_posix()
    preferred_doc_id = _safe_doc_id(file_path.name)
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
    return {
        "id": doc_id,
        "content": content,
        "metadata": {
            "source": "startup_ingest",
            "path": relative,
            "filename": file_path.name,
            "extension": file_path.suffix.lower(),
        },
    }


def _ingest_single_document(raw_doc: Dict[str, object]) -> int:
    normalized_docs = normalize_documents([raw_doc])
    if not normalized_docs:
        return 0

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


def run_startup_ingestion_if_needed() -> None:
    """Ingest files from INGEST_PATH only when vector store is empty."""
    ingest_root = Path(settings.INGEST_PATH).expanduser()
    logger.info("startup ingest begin: ingest_path=%s", ingest_root)

    collection = get_chroma_collection()
    current_count = collection.count()
    logger.info("startup ingest vector count before run=%s", current_count)
    if current_count > 0:
        logger.info("startup ingest skipped: vector store already populated")
        return

    if not ingest_root.exists() or not ingest_root.is_dir():
        logger.info("startup ingest skipped: ingest path not found or not a directory")
        return

    files = _list_candidate_files(ingest_root)
    logger.info("startup ingest files found=%s", len(files))
    if not files:
        logger.info("startup ingest skipped: no supported files")
        return

    total_files = 0
    total_vectors = 0
    used_doc_ids: Set[str] = set()

    for file_path in files:
        content = _read_file_content(file_path)
        if not content:
            logger.info("startup ingest skip empty file: %s", file_path)
            continue

        raw_doc = _build_raw_document(
            file_path=file_path,
            ingest_root=ingest_root,
            content=content,
            used_doc_ids=used_doc_ids,
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
        "startup ingest complete: processed_files=%s added_vectors=%s",
        total_files,
        total_vectors,
    )

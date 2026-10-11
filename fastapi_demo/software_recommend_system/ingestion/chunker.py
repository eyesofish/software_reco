import re
from dataclasses import dataclass
from typing import Any

# Sentence boundary punctuation (Chinese + English).
# Quotes are treated as sentence closers when they appear after punctuation.
_SENTENCE_BOUNDARY_PATTERN = re.compile(
    r".+?(?:[。！？；、.!?,;:](?:[\"“”'‘’])*|$)",
    flags=re.S,
)


@dataclass
class _TextSpan:
    text: str
    start: int
    end: int


def _validate_chunk_params(chunk_size: int, chunk_overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be >= 0")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")


def _trim_window(content: str, start: int, end: int) -> _TextSpan:
    """Trim whitespace for a [start, end) window while keeping absolute offsets."""
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return _TextSpan(text=content[start:end], start=start, end=end)


def _split_into_sentence_spans(content: str) -> list[_TextSpan]:
    """Split text into sentence-like spans using punctuation boundaries."""
    spans: list[_TextSpan] = []
    for match in _SENTENCE_BOUNDARY_PATTERN.finditer(content):
        span = _trim_window(content, *match.span())
        if span.text:
            spans.append(span)
    return spans


def _split_span_by_fixed_length(
    content: str,
    span: _TextSpan,
    chunk_size: int,
    chunk_overlap: int,
) -> list[_TextSpan]:
    """Fallback splitter for oversized single sentences."""
    pieces: list[_TextSpan] = []
    step = max(1, chunk_size - chunk_overlap)

    for offset in range(0, len(span.text), step):
        piece_start = span.start + offset
        piece_end = min(span.end, piece_start + chunk_size)
        piece = _trim_window(content, piece_start, piece_end)
        if piece.text:
            pieces.append(piece)
        if piece_end >= span.end:
            break

    return pieces


def _prepare_spans(content: str, chunk_size: int, chunk_overlap: int) -> list[_TextSpan]:
    """Sentence-first segmentation; oversized sentences fallback to fixed-length pieces."""
    sentence_spans = _split_into_sentence_spans(content)
    prepared: list[_TextSpan] = []

    for span in sentence_spans:
        if len(span.text) <= chunk_size:
            prepared.append(span)
        else:
            prepared.extend(
                _split_span_by_fixed_length(
                    content=content,
                    span=span,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )

    return prepared


def _next_cursor_with_overlap(
    spans: list[_TextSpan],
    start_idx: int,
    end_idx: int,
    chunk_overlap: int,
) -> int:
    """Carry tail sentences from current chunk into next chunk (sentence-level overlap)."""
    if chunk_overlap <= 0:
        return end_idx + 1

    overlap_len = 0
    next_cursor = end_idx + 1

    # Do not allow overlap to include the first sentence of current chunk,
    # otherwise cursor may not advance.
    for idx in range(end_idx, start_idx, -1):
        sent_len = len(spans[idx].text)
        if overlap_len + sent_len > chunk_overlap:
            break
        overlap_len += sent_len
        next_cursor = idx

    return next_cursor


def _build_chunks(
    content: str,
    spans: list[_TextSpan],
    chunk_size: int,
    chunk_overlap: int,
) -> list[_TextSpan]:
    """Group sentence spans into chunks under chunk_size, with sentence overlap."""
    chunks: list[_TextSpan] = []
    cursor = 0
    total = len(spans)

    while cursor < total:
        start_idx = cursor
        end_exclusive = start_idx

        # Greedy pack by sentences until adding the next sentence would exceed chunk_size.
        while end_exclusive < total:
            sentence_len = spans[end_exclusive].end - spans[start_idx].start
            if sentence_len > chunk_size and end_exclusive > start_idx:
                break
            if sentence_len > chunk_size:
                # Defensive fallback: should be rare because oversized spans are pre-split.
                break
            end_exclusive += 1

        end_idx = max(start_idx, end_exclusive - 1)

        raw_start = spans[start_idx].start
        raw_end = spans[end_idx].end
        chunk_span = _trim_window(content, raw_start, raw_end)
        if chunk_span.text:
            chunks.append(chunk_span)

        if end_idx >= total - 1:
            break

        next_cursor = _next_cursor_with_overlap(
            spans=spans,
            start_idx=start_idx,
            end_idx=end_idx,
            chunk_overlap=chunk_overlap,
        )
        cursor = next_cursor if next_cursor > start_idx else end_idx + 1

    return chunks


def _chunk_content_spans(
    content: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[_TextSpan]:
    spans = _prepare_spans(
        content=content,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if not spans:
        return []
    return _build_chunks(
        content=content,
        spans=spans,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def chunk_documents(
    documents: list[dict[str, Any]],
    chunk_size: int,
    chunk_overlap: int,
    *,
    strategy: str | None = None,
) -> list[dict[str, Any]]:
    """Split normalized docs by structure when available, otherwise by sentences.

    Structural spans preserve sections, tables, code, and original source offsets.
    Sentence fallback:
    1) Split by sentence boundaries (Chinese/English punctuation first).
    2) Greedily group sentences into chunks up to chunk_size.
    3) Carry tail sentences into next chunk for overlap.
    4) If a single sentence is longer than chunk_size, fallback to fixed-length split.
    """
    _validate_chunk_params(chunk_size, chunk_overlap)
    strategy = _resolve_strategy(strategy)

    chunks: list[dict[str, Any]] = []

    for doc in documents or []:
        if not isinstance(doc, dict):
            continue

        source_doc_id = str(doc.get("id", "")).strip()
        if not source_doc_id:
            continue

        content = str(doc.get("content", ""))
        if not content:
            continue

        base_metadata = doc.get("metadata", {})
        if not isinstance(base_metadata, dict):
            base_metadata = {}

        if strategy == "structure" and doc.get("blocks"):
            from .structural_chunker import structural_pieces

            for index, piece in enumerate(structural_pieces(doc, chunk_size, chunk_overlap)):
                chunks.append(
                    {
                        "id": f"{source_doc_id}:{index}",
                        "content": piece["content"],
                        "metadata": {
                            **base_metadata,
                            **piece["metadata"],
                            "source_doc_id": source_doc_id,
                            "chunk_index": index,
                        },
                    }
                )
            continue

        chunk_spans = _chunk_content_spans(
            content=content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for chunk_index, chunk_span in enumerate(chunk_spans):
            chunk_id = f"{source_doc_id}:{chunk_index}"
            metadata = {
                **base_metadata,
                "source_doc_id": source_doc_id,
                "chunk_index": chunk_index,
                "chunk_start": chunk_span.start,
                "chunk_end": chunk_span.end,
                "chunking_strategy": "sentence",
                "chunking_version": "sentence-v1",
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            }
            chunks.append(
                {
                    "id": chunk_id,
                    "content": chunk_span.text,
                    "metadata": metadata,
                }
            )

    return chunks


def chunk_documents_parent_child(
    documents: list[dict[str, Any]],
    parent_chunk_size: int,
    parent_chunk_overlap: int,
    child_chunk_size: int,
    child_chunk_overlap: int,
    *,
    strategy: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split docs with shared structural boundaries and parent linkage metadata."""
    _validate_chunk_params(parent_chunk_size, parent_chunk_overlap)
    _validate_chunk_params(child_chunk_size, child_chunk_overlap)
    strategy = _resolve_strategy(strategy)

    parent_chunks: list[dict[str, Any]] = []
    child_chunks: list[dict[str, Any]] = []

    for doc in documents or []:
        if not isinstance(doc, dict):
            continue

        source_doc_id = str(doc.get("id", "")).strip()
        if not source_doc_id:
            continue

        content = str(doc.get("content", ""))
        if not content:
            continue

        base_metadata = doc.get("metadata", {})
        if not isinstance(base_metadata, dict):
            base_metadata = {}

        if strategy == "structure" and doc.get("blocks"):
            from .structural_chunker import structural_pieces

            for parent_index, piece in enumerate(structural_pieces(doc, parent_chunk_size, parent_chunk_overlap)):
                parent_id = f"{source_doc_id}:p:{parent_index}"
                meta = {
                    **base_metadata,
                    **piece["metadata"],
                    "source_doc_id": source_doc_id,
                    "parent_id": parent_id,
                    "parent_index": parent_index,
                    "parent_start": piece["metadata"]["chunk_start"],
                    "parent_end": piece["metadata"]["chunk_end"],
                }
                parent_chunks.append({"id": parent_id, "content": piece["content"], "metadata": meta})
                children = structural_pieces(
                    doc, child_chunk_size, child_chunk_overlap, (meta["parent_start"], meta["parent_end"])
                )
                for child_index, child in enumerate(children):
                    child_chunks.append(
                        {
                            "id": f"{parent_id}:c:{child_index}",
                            "content": child["content"],
                            "metadata": {
                                **base_metadata,
                                **child["metadata"],
                                "source_doc_id": source_doc_id,
                                "parent_id": parent_id,
                                "child_index": child_index,
                            },
                        }
                    )
            continue

        parent_spans = _chunk_content_spans(
            content=content,
            chunk_size=parent_chunk_size,
            chunk_overlap=parent_chunk_overlap,
        )

        for parent_index, parent_span in enumerate(parent_spans):
            parent_id = f"{source_doc_id}:p:{parent_index}"
            parent_metadata = {
                **base_metadata,
                "source_doc_id": source_doc_id,
                "parent_id": parent_id,
                "parent_index": parent_index,
                "parent_start": parent_span.start,
                "parent_end": parent_span.end,
                "chunking_strategy": "sentence",
                "chunking_version": "sentence-v1",
                "chunk_size": parent_chunk_size,
                "chunk_overlap": parent_chunk_overlap,
            }
            parent_chunks.append(
                {
                    "id": parent_id,
                    "content": parent_span.text,
                    "metadata": parent_metadata,
                }
            )

            child_spans = _chunk_content_spans(
                content=parent_span.text,
                chunk_size=child_chunk_size,
                chunk_overlap=child_chunk_overlap,
            )
            for child_index, child_span in enumerate(child_spans):
                child_id = f"{parent_id}:c:{child_index}"
                child_metadata = {
                    **base_metadata,
                    "source_doc_id": source_doc_id,
                    "parent_id": parent_id,
                    "child_index": child_index,
                    "chunk_start": parent_span.start + child_span.start,
                    "chunk_end": parent_span.start + child_span.end,
                    "chunking_strategy": "sentence",
                    "chunking_version": "sentence-v1",
                    "chunk_size": child_chunk_size,
                    "chunk_overlap": child_chunk_overlap,
                }
                child_chunks.append(
                    {
                        "id": child_id,
                        "content": child_span.text,
                        "metadata": child_metadata,
                    }
                )

    return parent_chunks, child_chunks


def _resolve_strategy(strategy: str | None) -> str:
    if strategy is None:
        from ..config import settings

        strategy = settings.CHUNKING_STRATEGY
    if strategy not in {"structure", "sentence"}:
        raise ValueError("chunking strategy must be structure or sentence")
    return strategy

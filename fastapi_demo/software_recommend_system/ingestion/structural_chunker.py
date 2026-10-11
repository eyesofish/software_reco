"""Pack structural spans without losing source coordinates or splitting table rows."""

from typing import Any

from .structure import PARSER_VERSION


def _decoration(block: dict[str, Any], title: str, start: int, end: int) -> tuple[str, str]:
    if block["kind"] == "table":
        return title + (block["header"] + "\n" if start > block["start"] else ""), ""
    opening = f"{block['fence']}{block.get('language', '')}\n" if start > block["start"] else ""
    closing = "\n" + block["fence"] if end < block["end"] else ""
    return title + opening, closing


def structural_pieces(
    doc: dict[str, Any], size: int, overlap: int, bounds: tuple[int, int] | None = None
) -> list[dict[str, Any]]:
    from .chunker import _chunk_content_spans

    content = doc["content"]
    lower, upper = bounds or (0, len(content))
    pieces: list[dict[str, Any]] = []

    def emit(block: dict[str, Any], start: int, end: int, prefix: str = "", suffix: str = "", **extra: Any) -> None:
        body = content[start:end]
        text = prefix + body + suffix
        meta = {
            key: block[key]
            for key in (
                "section_path",
                "section_id",
                "page_start",
                "page_end",
                "table_id",
                "fallback_reason",
                "language",
            )
            if key in block
        }
        meta.update(
            block_kind=block["kind"],
            chunk_start=start,
            chunk_end=end,
            content_body_start=len(prefix),
            content_body_end=len(prefix) + len(body),
            oversized=len(text) > size,
            chunking_strategy="structure",
            chunking_version=PARSER_VERSION,
            chunk_size=size,
            chunk_overlap=overlap,
            **extra,
        )
        pieces.append({"content": text, "metadata": meta})

    blocks = doc["blocks"]
    cursor = 0
    while cursor < len(blocks):
        block = blocks[cursor]
        cursor += 1
        start, end = max(lower, block["start"]), min(upper, block["end"])
        if start >= end:
            continue
        section = block.get("section_path", "")
        title_prefix = f"{section}\n\n" if section else ""
        if block["kind"] in {"paragraph", "heading"}:
            # Merge adjacent prose only within a section and page. Preserve paragraph boundaries.
            paragraphs = [(start, end)]
            while cursor < len(blocks):
                nxt = blocks[cursor]
                if (
                    nxt["kind"] not in {"paragraph", "heading"}
                    or nxt.get("section_id") != block.get("section_id")
                    or nxt.get("page_start") != block.get("page_start")
                    or nxt["start"] >= upper
                ):
                    break
                paragraphs.append((max(lower, nxt["start"]), min(upper, nxt["end"])))
                cursor += 1
            budget = max(1, size - len(title_prefix))
            pending: tuple[int, int] | None = None
            for pstart, pend in paragraphs:
                if pending and pend - pending[0] <= budget:
                    pending = (pending[0], pend)
                    continue
                if pending:
                    emit(block, *pending, prefix=title_prefix)
                    pending = None
                if pend - pstart <= budget:
                    pending = (pstart, pend)
                else:
                    spans = _chunk_content_spans(content[pstart:pend], budget, min(overlap, budget - 1))
                    for span in spans:
                        emit(block, pstart + span.start, pstart + span.end, prefix=title_prefix)
            if pending:
                emit(block, *pending, prefix=title_prefix)
            continue

        if block["kind"] == "table":
            units = [(a, b, i + 1) for i, (a, b) in enumerate(block["row_spans"]) if a >= lower and b <= upper]
            if not units:
                if not block["row_spans"]:
                    emit(block, start, end, prefix=title_prefix, table_row_start=0, table_row_end=0)
                continue
            if start == block["start"]:
                units[0] = (start, units[0][1], units[0][2])

        else:  # Code: atomic lines, with opening/closing fences in the original span when possible.
            text = content[block["start"] : block["end"]]
            lines = text.splitlines(keepends=True)
            units = []
            pos = block["start"]
            for line in lines:
                units.append((pos, pos + len(line), 0))
                pos += len(line)
            # Attach delimiters to data lines so they cannot become empty standalone chunks.
            if len(units) > 2:
                units[1] = (units[0][0], units[1][1], 0)
                units[-2] = (units[-2][0], units[-1][1], 0)
                units = units[1:-1]
            else:
                units = [(block["start"], block["end"], 0)]
            units = [unit for unit in units if unit[0] >= lower and unit[1] <= upper]

        idx = 0
        while idx < len(units):
            first = idx
            a, b, _ = units[idx]
            idx += 1
            while idx < len(units):
                candidate_end = units[idx][1]
                prefix, suffix = _decoration(block, title_prefix, a, candidate_end)
                if len(prefix) + candidate_end - a + len(suffix) > size:
                    break
                b = candidate_end
                idx += 1
            prefix, suffix = _decoration(block, title_prefix, a, b)
            extra = {}
            if block["kind"] == "table":
                extra = {"table_row_start": units[first][2], "table_row_end": units[idx - 1][2]}
            emit(block, a, b, prefix, suffix, **extra)
    return pieces

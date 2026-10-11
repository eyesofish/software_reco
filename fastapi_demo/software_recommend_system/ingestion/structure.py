"""Deterministic structural parsing; offsets refer to the returned canonical text."""

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
PARSER_VERSION = "structure-v1"
MAX_HEADING_LENGTH = 100
HEADING_SIZE_RATIO = 1.2
NUMBERED_HEADING = re.compile(r"^(?:第[一二三四五六七八九十百\d]+[章节篇]|\d+(?:\.\d+)*[.、]?\s+\S)")


def _cell(value: Any) -> str:
    return str("" if value is None else value).strip().replace("|", r"\|").replace("\n", "<br>")


def _row(cells: list[Any]) -> str:
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def table_block(rows: list[list[Any]], has_header: bool, **metadata: Any) -> dict[str, Any]:
    width = max((len(row) for row in rows), default=0)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    header = padded.pop(0) if has_header and padded else [f"Column {i + 1}" for i in range(width)]
    header_text = _row(header) + "\n" + _row(["---"] * width)
    return {"kind": "table", "header": header_text, "rows": [_row(row) for row in padded], **metadata}


def assemble_blocks(blocks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Create one canonical source and absolute offsets, including table header spans."""
    parts: list[str] = []
    result = []
    offset = 0
    for raw in blocks:
        block = dict(raw)
        if block["kind"] == "table":
            text = "\n".join([block["header"], *block["rows"]])
        else:
            text = str(block.pop("text", "")).strip()
        if not text:
            continue
        if parts:
            offset += 2
        block.update(start=offset, end=offset + len(text))
        if block["kind"] == "table":
            row_offset = offset + len(block["header"]) + 1
            block["row_spans"] = []
            for row in block.pop("rows"):
                block["row_spans"].append((row_offset, row_offset + len(row)))
                row_offset += len(row) + 1
            block["table_id"] = f"table-{sum(b['kind'] == 'table' for b in result)}"
        parts.append(text)
        result.append(block)
        offset += len(text)
    return "\n\n".join(parts), result


def parse_markdown(content: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").enable("table").parse(content)
    lines = content.splitlines(keepends=True)
    blocks: list[dict[str, Any]] = []
    headings: list[tuple[int, str]] = []
    section_id = 0
    i = 0
    last_line = 0
    while i < len(tokens):
        token = tokens[i]
        if token.level != 0 or token.map is None or token.nesting == -1:
            i += 1
            continue
        start, end = token.map
        gap = "".join(lines[last_line:start]).strip()
        if gap:
            blocks.append(
                {
                    "kind": "paragraph",
                    "text": gap,
                    "section_id": section_id,
                    "section_path": " > ".join(name for _, name in headings),
                }
            )
        last_line = end
        text = "".join(lines[start:end]).strip()
        kind = "paragraph"
        if token.type == "heading_open":
            level = int(token.tag[1:])
            title = tokens[i + 1].content
            headings = [(depth, name) for depth, name in headings if depth < level]
            headings.append((level, title))
            section_id += 1
            kind = "heading"
        meta = {"section_path": " > ".join(name for _, name in headings), "section_id": section_id}
        if token.type == "table_open":
            rows: list[list[str]] = []
            j = i + 1
            while j < len(tokens) and tokens[j].type != "table_close":
                if tokens[j].type == "tr_open":
                    rows.append([])
                elif tokens[j].type == "inline" and rows:
                    rows[-1].append(tokens[j].content)
                j += 1
            # markdown-it unescapes pipe escapes in inline.content; serialization re-escapes them.
            blocks.append(table_block(rows, True, **meta))
            i = j + 1
            continue
        if token.type in {"fence", "code_block"}:
            kind = "code"
        block = {"kind": kind, "text": text, **meta}
        if kind == "code":
            block["language"] = token.info.strip().split()[0] if token.info.strip() else ""
            # Canonicalize fences so continuation chunks can repeat valid delimiters.
            fence = "`" * max(3, max((len(m[0]) for m in re.finditer(r"`+", token.content)), default=0) + 1)
            block["fence"] = fence
            block["text"] = f"{fence}{block['language']}\n{token.content.rstrip()}\n{fence}"
        blocks.append(block)
        if token.nesting == 1:
            i += 1
            while i < len(tokens) and not (tokens[i].level == 0 and tokens[i].nesting == -1):
                i += 1
        i += 1
    tail = "".join(lines[last_line:]).strip()
    if tail:
        blocks.append(
            {
                "kind": "paragraph",
                "text": tail,
                "section_id": section_id,
                "section_path": " > ".join(name for _, name in headings),
            }
        )
    text, blocks = assemble_blocks(blocks)
    return text, blocks, []


def _inside(char: dict[str, Any], bbox: tuple[float, ...]) -> bool:
    x = (char["x0"] + char["x1"]) / 2
    y = (char["top"] + char["bottom"]) / 2
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _outline_titles(path: Path) -> dict[int, dict[str, tuple[int, str]]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    result: dict[int, dict[str, tuple[int, str]]] = {}

    def visit(items: list[Any], depth: int = 1) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item, depth + 1)
            else:
                page = reader.get_destination_page_number(item)
                title = str(item.title).strip()
                result.setdefault(page, {})[re.sub(r"\s+", "", title).casefold()] = (depth, title)

    visit(reader.outline)
    return result


def parse_pdf(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    import pdfplumber

    warnings: list[str] = []
    try:
        outlines = _outline_titles(path)
    except Exception:
        outlines = {}
        warnings.append("outline_unavailable")
    blocks: list[dict[str, Any]] = []
    headings: list[tuple[int, str]] = []
    section_id = 0
    with pdfplumber.open(path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_no = page_index + 1
            if not page.chars:
                warnings.append(f"page_{page_no}:needs_ocr" if page.images else f"page_{page_no}:empty_page")
                continue
            try:
                tables = page.find_tables()
            except Exception:
                tables = []
                warnings.append(f"page_{page_no}:table_detection_failed")
            extracted = []
            for table in tables:
                try:
                    rows = table.extract()
                except Exception:
                    warnings.append(f"page_{page_no}:table_extraction_failed")
                    continue  # Keep failed regions in the prose extraction instead of dropping their text.
                if rows:
                    extracted.append((table, rows))
            text_page = page.filter(
                lambda obj, regions=tuple(t.bbox for t, _ in extracted): (
                    not any(_inside(obj, bbox) for bbox in regions) if obj.get("object_type") == "char" else True
                )
            )
            lines = text_page.extract_text_lines(return_chars=True)
            sizes = Counter(round(c["size"], 1) for c in text_page.chars if c.get("text", "").strip())
            body_size = sizes.most_common(1)[0][0] if sizes else 12.0
            events: list[tuple[float, str, Any]] = [(line["top"], "line", line) for line in lines]
            events.extend((table.bbox[1], "table", (table, rows)) for table, rows in extracted)
            page_has_heading = False
            page_blocks_start = len(blocks)
            previous_bottom = -1.0
            for _, kind, data in sorted(events, key=lambda event: event[0]):
                meta = {
                    "page_start": page_no,
                    "page_end": page_no,
                    "section_id": section_id,
                    "section_path": " > ".join(name for _, name in headings),
                }
                if kind == "table":
                    table, rows = data
                    first_box = table.rows[0].bbox
                    first_chars = [c for c in page.chars if _inside(c, first_box)]
                    other_chars = [c for c in page.chars if _inside(c, table.bbox) and not _inside(c, first_box)]
                    first_bold = bool(first_chars) and all("bold" in c["fontname"].lower() for c in first_chars)
                    other_bold = bool(other_chars) and all("bold" in c["fontname"].lower() for c in other_chars)
                    first_size = max((c["size"] for c in first_chars), default=0)
                    other_size = max((c["size"] for c in other_chars), default=0)
                    has_header = bool(other_chars) and (
                        (first_bold and not other_bold) or first_size >= HEADING_SIZE_RATIO * other_size
                    )
                    blocks.append(table_block(rows, has_header, **meta))
                    previous_bottom = table.bbox[3]
                    continue
                text = data["text"].strip()
                key = re.sub(r"\s+", "", text).casefold()
                bookmark = outlines.get(page_index, {}).get(key)
                font_size = max((c["size"] for c in data["chars"]), default=body_size)
                is_heading = len(text) <= MAX_HEADING_LENGTH and (
                    bookmark or NUMBERED_HEADING.match(text) or font_size >= body_size * HEADING_SIZE_RATIO
                )
                if is_heading:
                    level = (
                        bookmark[0]
                        if bookmark
                        else (
                            len(re.match(r"\d+(?:\.\d+)*", text)[0].split("."))
                            if re.match(r"\d+(?:\.\d+)*", text)
                            else 1
                        )
                    )
                    headings = [(depth, name) for depth, name in headings if depth < level]
                    headings.append((level, text))
                    section_id += 1
                    meta.update(section_id=section_id, section_path=" > ".join(name for _, name in headings))
                    page_has_heading = True
                # Join adjacent body lines into paragraphs, but never across pages or tables.
                if (
                    not is_heading
                    and len(blocks) > page_blocks_start
                    and blocks[-1]["kind"] == "paragraph"
                    and data["top"] - previous_bottom <= body_size * 0.8
                ):
                    blocks[-1]["text"] += "\n" + text
                else:
                    blocks.append({"kind": "heading" if is_heading else "paragraph", "text": text, **meta})
                previous_bottom = data["bottom"]
            if not page_has_heading and not headings:
                warnings.append(f"page_{page_no}:page_paragraph_fallback")
                for block in blocks[page_blocks_start:]:
                    block["fallback_reason"] = "page_paragraph_fallback"
    for warning in warnings:
        logger.warning("pdf.parse file=%s reason=%s", path.name, warning)
    text, blocks = assemble_blocks(blocks)
    return text, blocks, warnings

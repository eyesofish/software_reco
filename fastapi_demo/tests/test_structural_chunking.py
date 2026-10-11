from unittest.mock import MagicMock

import pytest
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from software_recommend_system.config import settings
from software_recommend_system.ingestion.chunker import chunk_documents, chunk_documents_parent_child
from software_recommend_system.ingestion.loader import normalize_documents
from software_recommend_system.ingestion.structure import parse_pdf


@pytest.fixture(autouse=True)
def structure_enabled(monkeypatch):
    monkeypatch.setattr(settings, "CHUNKING_STRATEGY", "structure")


def markdown_doc(text):
    return normalize_documents([{"id": "guide", "content": text, "metadata": {"extension": ".md"}}])[0]


def assert_source_ranges(doc, chunks):
    for chunk in chunks:
        meta = chunk["metadata"]
        assert (
            chunk["content"][meta["content_body_start"] : meta["content_body_end"]]
            == (doc["content"][meta["chunk_start"] : meta["chunk_end"]])
        )
        assert all(isinstance(value, (str, int, float, bool)) for value in meta.values())


def test_markdown_hierarchy_setext_empty_sections_and_fenced_hash():
    doc = markdown_doc(
        "前言。\n\nRoot\n====\nIntro.\n\n## Child\n正文。\n\n"
        "```python\n# not a heading\nprint(1)\n```\n\n## Empty\n\n# Last\n尾声。"
    )
    chunks = chunk_documents([doc], 200, 20)
    assert {c["metadata"]["section_path"] for c in chunks} == {"", "Root", "Root > Child", "Root > Empty", "Last"}
    assert any("# not a heading" in c["content"] and c["metadata"]["block_kind"] == "code" for c in chunks)
    assert all(not ("正文" in c["content"] and "尾声" in c["content"]) for c in chunks)
    assert_source_ranges(doc, chunks)


def test_markdown_nested_lists_and_reference_text_preserved():
    doc = markdown_doc("# Usage\n\n- one\n  - two\n\n> quoted\n\n[site][ref].\n\n[ref]: https://example.com")
    combined = "\n".join(c["content"] for c in chunk_documents([doc], 500, 20))
    for needle in ("one", "two", "quoted", "https://example.com"):
        assert needle in combined


def test_bom_does_not_hide_first_heading():
    doc = markdown_doc("\ufeff# Heading\n\nBody.")
    assert doc["blocks"][0]["section_path"] == "Heading"


def test_long_prose_real_length_budget_and_overlap():
    doc = markdown_doc("# Guide\n\n" + "Sentence with spaces.   " * 40 + "\n\n尾段。")
    chunks = chunk_documents([doc], 90, 20)
    assert all(len(c["content"]) <= 90 for c in chunks)
    assert any("尾段" in c["content"] for c in chunks)
    assert_source_ranges(doc, chunks)


def test_large_table_repeats_header_preserves_rows_and_escaped_pipe():
    doc = markdown_doc(
        "# Products\n\n| Name | Value |\n| --- | --- |\n"
        + "\n".join(f"| item{i} | a\\|b<br>line{i} |" for i in range(12))
    )
    chunks = [c for c in chunk_documents([doc], 105, 20) if c["metadata"]["block_kind"] == "table"]
    assert len(chunks) > 1
    assert all("| Name | Value |" in c["content"] for c in chunks)
    assert all(len(c["content"]) <= 105 for c in chunks)
    rows = [n for c in chunks for n in range(c["metadata"]["table_row_start"], c["metadata"]["table_row_end"] + 1)]
    assert rows == list(range(1, 13))
    assert sum(c["content"].count("item7") for c in chunks) == 1
    assert all(r"a\|b" in c["content"] for c in chunks)
    assert_source_ranges(doc, chunks)


def test_small_table_and_oversized_row():
    doc = markdown_doc("| Key | Value |\n| --- | --- |\n| x | y |")
    assert len(chunk_documents([doc], 100, 10)) == 1
    doc = markdown_doc("| Key | Value |\n| --- | --- |\n| x | " + "z" * 200 + " |")
    chunks = chunk_documents([doc], 60, 10)
    assert len(chunks) == 1 and chunks[0]["metadata"]["oversized"]
    assert "z" * 200 in chunks[0]["content"]


def test_code_chunks_have_fences_language_and_atomic_lines():
    doc = markdown_doc("# Code\n\n```python\n" + "\n".join(f"print('line{i}')" for i in range(12)) + "\n```")
    chunks = [c for c in chunk_documents([doc], 70, 10) if c["metadata"]["block_kind"] == "code"]
    assert len(chunks) > 1
    assert all("```python\n" in c["content"] and c["content"].endswith("```") for c in chunks)
    for i in range(12):
        assert sum(c["content"].count(f"print('line{i}')") for c in chunks) == 1
    assert_source_ranges(doc, chunks)


def test_oversized_code_line_and_header_only_table_keep_content():
    doc = markdown_doc("```text\n" + "x" * 150 + "\n```")
    chunks = chunk_documents([doc], 50, 5)
    assert len(chunks) == 1 and chunks[0]["metadata"]["oversized"]
    assert "x" * 150 in chunks[0]["content"]
    doc = markdown_doc("| Name | Value |\n| --- | --- |")
    chunks = chunk_documents([doc], 100, 5)
    assert len(chunks) == 1 and "Name" in chunks[0]["content"]
    assert chunks[0]["metadata"]["table_row_start"] == 0


def test_source_reconstruction_excludes_repeated_table_headers():
    from app.api.v1.startup_ingest import _read_indexed_content

    doc = markdown_doc("| Name | Value |\n| --- | --- |\n" + "\n".join(f"| row{i} | value{i} |" for i in range(8)))
    chunks = chunk_documents([doc], 65, 5)
    collection = MagicMock()
    collection.get.return_value = {
        "documents": [c["content"] for c in chunks],
        "metadatas": [c["metadata"] for c in chunks],
    }
    rebuilt = _read_indexed_content(collection, "guide.md")
    assert rebuilt == doc["content"]


def test_parent_child_structures_and_original_offsets():
    doc = markdown_doc(
        "# A\n\n"
        + "Body sentence. " * 30
        + "\n\n| N | V |\n| --- | --- |\n"
        + "\n".join(f"| {i} | value{i} |" for i in range(12))
        + "\n\n```python\n"
        + "print('line')\n" * 12
        + "```\n\n# B\nLast."
    )
    parents, children = chunk_documents_parent_child([doc], 180, 20, 70, 10)
    by_id = {c["id"]: c for c in parents}
    assert {c["metadata"]["parent_id"] for c in children} == set(by_id)
    for child in children:
        meta = child["metadata"]
        parent = by_id[meta["parent_id"]]["metadata"]
        assert parent["parent_start"] <= meta["chunk_start"] < meta["chunk_end"] <= parent["parent_end"]
        assert parent["section_path"] == meta["section_path"]
    assert_source_ranges(doc, parents + children)


def test_sentence_mode_and_plain_text_compatibility(monkeypatch):
    monkeypatch.setattr(settings, "CHUNKING_STRATEGY", "sentence")
    doc = markdown_doc("# A\nOne.\n\n# B\nTwo.")
    assert "blocks" not in doc
    assert len(chunk_documents([doc], 100, 10)) == 1
    plain = {"id": "plain", "content": "one.\n\n     two." * 12, "metadata": {}}
    assert all(len(c["content"]) <= 25 for c in chunk_documents([plain], 25, 5))


def make_pdf(path, header=True):
    c = canvas.Canvas(str(path))
    c.bookmarkPage("intro")
    c.addOutlineEntry("Introduction", "intro")
    # Bookmark title deliberately uses body font to exercise outline matching.
    c.setFont("Helvetica", 11)
    c.drawString(50, 800, "Introduction")
    for i in range(5):
        c.drawString(50, 780 - i * 14, "This is a normal body paragraph containing useful evidence.")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 680, "2. Configuration")
    data = [["Name", "Value"], ["redis", "cluster\nenabled"], ["sqlite", "local"]]
    table = Table(data, colWidths=[120, 140])
    commands = [("GRID", (0, 0), (-1, -1), 1, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 11)]
    if header:
        commands.append(("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"))
    table.setStyle(TableStyle(commands))
    table.wrapOn(c, 400, 200)
    table.drawOn(c, 50, 550)
    c.showPage()
    c.setFont("Helvetica", 11)
    c.drawString(50, 790, "Continued configuration evidence on the next page.")
    c.showPage()
    c.showPage()  # empty page
    image = Image.new("RGB", (40, 40), "white")
    c.drawImage(ImageReader(image), 50, 700, 100, 100)
    c.showPage()
    c.save()


@pytest.mark.parametrize("header", [True, False])
def test_actual_pdf_bookmarks_headings_tables_pages_and_ocr_notice(tmp_path, header):
    path = tmp_path / "guide.pdf"
    make_pdf(path, header)
    text, blocks, warnings = parse_pdf(path)
    assert "page_3:empty_page" in warnings
    assert "page_4:needs_ocr" in warnings
    assert text.count("redis") == 1  # table contents excluded from prose extraction
    table = next(b for b in blocks if b["kind"] == "table")
    assert len(table["row_spans"]) == (2 if header else 3)
    assert ("| Name | Value |" if header else "| Column 1 | Column 2 |") == table["header"].splitlines()[0]
    assert "cluster<br>enabled" in text
    doc = {"id": "pdf", "content": text, "blocks": blocks, "metadata": {}}
    chunks = chunk_documents([doc], 180, 20)
    assert any(c["metadata"]["section_path"] == "Introduction" for c in chunks)
    assert any(c["metadata"]["section_path"] == "2. Configuration" and c["metadata"]["page_start"] == 2 for c in chunks)
    assert_source_ranges(doc, chunks)


def test_pdf_without_headings_falls_back_to_page(tmp_path):
    path = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 11)
    c.drawString(50, 700, "A plain paragraph without a section heading.")
    c.save()
    _, blocks, warnings = parse_pdf(path)
    assert warnings == ["page_1:page_paragraph_fallback"]
    assert blocks[0]["fallback_reason"] == "page_paragraph_fallback"


def test_structure_metadata_survives_vector_and_keyword_normalization():
    from software_recommend_system.document_schema import Document
    from software_recommend_system.keyword_index import KeywordIndex
    from software_recommend_system.retriever import _normalize_documents
    from software_recommend_system.tools import _metadata_obj

    meta = {
        "source": "test",
        "section_path": "A > B",
        "page_start": 3,
        "page_end": 4,
        "table_id": "table-1",
        "table_row_start": 2,
        "table_row_end": 5,
    }
    doc = Document(content="body", metadata=_metadata_obj(meta), score=1)
    normalized = _normalize_documents([doc], "vector", "vector")[0]
    assert normalized.metadata.section_path == "A > B"
    client = MagicMock()
    client.search.return_value = {
        "hits": {"hits": [{"_id": "x", "_score": 1, "_source": {"content": "body", "metadata": meta}}]}
    }
    hit = KeywordIndex(client, "test").search("query", 1)[0]
    assert hit.metadata.page_start == 3 and hit.metadata.table_row_end == 5

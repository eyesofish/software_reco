from typing import Any


def normalize_documents(raw_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw docs into a stable schema used by the ingest pipeline.

    Input item shape:
    - id: str-like (preferred, optional fallback generated)
    - content: str-like
    - metadata: optional dict
    """
    normalized: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_documents or []):
        if not isinstance(raw, dict):
            continue

        content = str(raw.get("content", "")).strip()
        if not content:
            continue

        doc_id = str(raw.get("id") or f"doc-{index}").strip()
        if not doc_id:
            doc_id = f"doc-{index}"

        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        normalized.append(
            {
                "id": doc_id,
                "content": content,
                "metadata": metadata,
            }
        )

    return normalized

"""Convert a HuggingFace dataset split into a LangSmith RAG eval dataset.

Example:
  python -m evaluation.convert_hf_to_langsmith \
    --hf-dataset nvidia/TechQA-RAG-Eval \
    --hf-split test \
    --langsmith-dataset rag_hitl_eval_v1 \
    --overwrite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import NAMESPACE_URL, uuid5

from datasets import Dataset, DatasetDict, load_dataset
from dotenv import load_dotenv
from langsmith import Client


DEFAULT_HF_DATASET = "nvidia/TechQA-RAG-Eval"
DEFAULT_HF_SPLIT = "test"
DEFAULT_LANGSMITH_DATASET = "rag_hitl_eval_v1"
DEFAULT_BATCH_SIZE = 200


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_present_key(keys: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    key_set = set(keys)
    for candidate in candidates:
        if candidate in key_set:
            return candidate
    return None


def _pick_first_non_empty(values: Iterable[Any]) -> str:
    for value in values:
        text = _stringify(value)
        if text:
            return text
    return ""


def _short_sha1(parts: Iterable[str]) -> str:
    joined = "||".join([p for p in parts if p])
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _stable_example_id(langsmith_dataset: str, source_dataset: str, source_id: str) -> str:
    unique_key = f"langsmith://{langsmith_dataset}/{source_dataset}/{source_id}"
    return str(uuid5(NAMESPACE_URL, unique_key))


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _extract_gold_doc_ids(contexts: Any) -> List[str]:
    if not isinstance(contexts, list):
        return []

    doc_ids: List[str] = []
    for idx, ctx in enumerate(contexts):
        if isinstance(ctx, dict):
            explicit_id = _pick_first_non_empty(
                [
                    ctx.get("doc_id"),
                    ctx.get("document_id"),
                    ctx.get("evidence_id"),
                    ctx.get("id"),
                    ctx.get("filename"),
                ]
            )
            if explicit_id:
                doc_ids.append(explicit_id)
                continue

            stable_hint = _pick_first_non_empty(
                [
                    _stringify(ctx.get("url")),
                    _stringify(ctx.get("title")),
                    _stringify(ctx.get("text")),
                    _stringify(ctx.get("chunk")),
                ]
            )
            if stable_hint:
                doc_ids.append(f"doc_{_short_sha1([stable_hint, str(idx)])}")
                continue

        fallback = _stringify(ctx)
        if fallback:
            doc_ids.append(f"doc_{_short_sha1([fallback, str(idx)])}")

    return _dedupe_keep_order([doc_id for doc_id in doc_ids if doc_id])


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    return normalized or "dataset"


def _resolve_split(dataset_dict: DatasetDict, requested_split: str) -> str:
    splits = list(dataset_dict.keys())
    if requested_split in dataset_dict:
        return requested_split

    preferred_order = ["test", "validation", "train"]
    for split in preferred_order:
        if split in dataset_dict:
            print(
                f"[WARN] Requested split '{requested_split}' not found. "
                f"Falling back to '{split}'."
            )
            return split

    fallback = splits[0]
    print(
        f"[WARN] Requested split '{requested_split}' not found. "
        f"Falling back to '{fallback}'."
    )
    return fallback


def _inspect_dataset(dataset_dict: DatasetDict) -> None:
    print(f"HF dataset splits: {list(dataset_dict.keys())}")
    for split_name in dataset_dict.keys():
        split_ds: Dataset = dataset_dict[split_name]
        print(f"\n[HF inspect] split={split_name} rows={len(split_ds)}")
        print(f"[HF inspect] fields={list(split_ds.features.keys())}")
        first_row = split_ds[0] if len(split_ds) > 0 else {}
        print(
            "[HF inspect] sample[0]="
            f"{json.dumps(first_row, ensure_ascii=False, default=str)[:1600]}"
        )


def _select_field_names(split_ds: Dataset) -> Dict[str, Optional[str]]:
    keys = list(split_ds.features.keys())
    return {
        "id": _first_present_key(keys, ["id", "example_id", "uid", "question_id"]),
        "question": _first_present_key(keys, ["question", "query", "prompt", "input"]),
        "answer": _first_present_key(
            keys,
            ["answer", "reference_answer", "gold_answer", "target", "output"],
        ),
        "contexts": _first_present_key(
            keys,
            ["contexts", "context", "documents", "evidences", "passages"],
        ),
    }


def _convert_rows(
    split_ds: Dataset,
    *,
    split_name: str,
    source_dataset: str,
    langsmith_dataset: str,
    max_samples: Optional[int],
) -> Tuple[List[Dict[str, Any]], Counter, List[Dict[str, Any]]]:
    field_names = _select_field_names(split_ds)
    print(f"[Mapping] field_names={field_names}")

    if not field_names["question"]:
        raise ValueError("Could not find a question field in the HuggingFace split.")
    if not field_names["answer"]:
        raise ValueError("Could not find an answer/reference field in the HuggingFace split.")

    total = len(split_ds) if max_samples is None else min(len(split_ds), max_samples)
    skip_reasons: Counter = Counter()
    converted_examples: List[Dict[str, Any]] = []
    preview_rows: List[Dict[str, Any]] = []

    for row_index in range(total):
        try:
            row = split_ds[row_index]
            source_id = _stringify(row.get(field_names["id"])) if field_names["id"] else ""
            if not source_id:
                source_id = f"{split_name}_{row_index:06d}"

            question = _stringify(row.get(field_names["question"]))
            if not question:
                skip_reasons["missing_question"] += 1
                continue

            reference_answer = _stringify(row.get(field_names["answer"]))
            if not reference_answer:
                skip_reasons["missing_reference_answer"] += 1
                continue

            contexts = row.get(field_names["contexts"]) if field_names["contexts"] else None
            gold_doc_ids = _extract_gold_doc_ids(contexts)

            metadata = {
                "split": split_name,
                "source_dataset": source_dataset,
                "source_id": source_id,
            }

            langsmith_example = {
                "id": _stable_example_id(langsmith_dataset, source_dataset, source_id),
                "inputs": {
                    "question": question,
                    "oracle_edits": [],
                },
                "outputs": {
                    "reference_answer": reference_answer,
                    "gold_doc_ids": gold_doc_ids,
                    "expected_mode": "rag",
                },
                "metadata": metadata,
                "split": split_name,
            }

            converted_examples.append(langsmith_example)
            preview_rows.append(
                {
                    "id": langsmith_example["id"],
                    "source_id": source_id,
                    "question": question,
                    "reference_answer": reference_answer,
                    "gold_doc_ids": gold_doc_ids,
                }
            )
        except Exception as exc:
            skip_reasons[f"row_error:{type(exc).__name__}"] += 1

    return converted_examples, skip_reasons, preview_rows


def _write_preview_jsonl(
    examples: List[Dict[str, Any]],
    *,
    source_dataset: str,
    split_name: str,
    langsmith_dataset: str,
) -> Path:
    output_dir = Path(__file__).resolve().parent / "previews"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"hf_to_langsmith_{_slugify(langsmith_dataset)}_"
        f"{_slugify(source_dataset)}_{_slugify(split_name)}.jsonl"
    )
    output_path = output_dir / filename
    with output_path.open("w", encoding="utf-8") as f:
        for example in examples:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
    return output_path


def _ensure_langsmith_dataset(
    client: Client,
    dataset_name: str,
    *,
    overwrite: bool,
    source_dataset: str,
    split_name: str,
) -> None:
    exists = client.has_dataset(dataset_name=dataset_name)
    if exists and overwrite:
        print(f"[LangSmith] Deleting existing dataset '{dataset_name}' (overwrite enabled).")
        client.delete_dataset(dataset_name=dataset_name)
        exists = False

    if not exists:
        description = (
            "RAG evaluation dataset converted from "
            f"{source_dataset} (split={split_name}) for local HITL/RAG eval."
        )
        client.create_dataset(dataset_name=dataset_name, description=description)
        print(f"[LangSmith] Created dataset '{dataset_name}'.")
    else:
        print(
            f"[LangSmith] Reusing dataset '{dataset_name}' "
            "(create only missing rows by stable example id)."
        )


def _upload_examples(
    client: Client,
    dataset_name: str,
    examples: List[Dict[str, Any]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Tuple[int, int, Counter]:
    failure_reasons: Counter = Counter()
    created_count = 0

    existing_ids = {str(example.id) for example in client.list_examples(dataset_name=dataset_name)}
    to_create = [example for example in examples if str(example["id"]) not in existing_ids]
    skipped_existing = len(examples) - len(to_create)

    for start in range(0, len(to_create), batch_size):
        chunk = to_create[start : start + batch_size]
        try:
            client.create_examples(dataset_name=dataset_name, examples=chunk, max_concurrency=3)
            created_count += len(chunk)
        except Exception:
            for example in chunk:
                try:
                    client.create_examples(dataset_name=dataset_name, examples=[example])
                    created_count += 1
                except Exception as item_exc:
                    failure_reasons[f"create_error:{type(item_exc).__name__}"] += 1

    return created_count, skipped_existing, failure_reasons


def _print_preview_samples(preview_rows: List[Dict[str, Any]], limit: int = 3) -> None:
    print("\n[Preview] First converted samples:")
    for idx, row in enumerate(preview_rows[:limit], start=1):
        compact = {
            "source_id": row["source_id"],
            "question": row["question"][:140],
            "reference_answer": row["reference_answer"][:140],
            "gold_doc_ids": row["gold_doc_ids"][:3],
        }
        print(f"{idx}. {json.dumps(compact, ensure_ascii=False)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert HuggingFace dataset rows to LangSmith RAG evaluation examples."
    )
    parser.add_argument("--hf-dataset", default=DEFAULT_HF_DATASET, help="HF dataset path")
    parser.add_argument("--hf-split", default=DEFAULT_HF_SPLIT, help="HF split name")
    parser.add_argument(
        "--langsmith-dataset",
        default=DEFAULT_LANGSMITH_DATASET,
        help="Target LangSmith dataset name",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Only process first N rows (optional)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview conversion and write local JSONL without uploading",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete and recreate existing LangSmith dataset before upload",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    root_dir = Path(__file__).resolve().parents[1]
    load_dotenv(root_dir / ".env")

    print(f"[Load] hf_dataset={args.hf_dataset} requested_split={args.hf_split}")
    dataset_dict = load_dataset(args.hf_dataset)
    _inspect_dataset(dataset_dict)

    split_name = _resolve_split(dataset_dict, args.hf_split)
    split_ds = dataset_dict[split_name]
    print(f"\n[Load] using_split={split_name} rows={len(split_ds)}")

    converted_examples, skip_reasons, preview_rows = _convert_rows(
        split_ds,
        split_name=split_name,
        source_dataset=args.hf_dataset,
        langsmith_dataset=args.langsmith_dataset,
        max_samples=args.max_samples,
    )

    preview_path = _write_preview_jsonl(
        converted_examples,
        source_dataset=args.hf_dataset,
        split_name=split_name,
        langsmith_dataset=args.langsmith_dataset,
    )
    print(f"[Output] preview_jsonl={preview_path}")
    _print_preview_samples(preview_rows, limit=3)

    print(
        "\n[Stats] converted="
        f"{len(converted_examples)} skipped={sum(skip_reasons.values())} "
        f"skip_reasons={dict(skip_reasons)}"
    )

    if args.dry_run:
        print("[Dry Run] Skipping LangSmith upload.")
        return

    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is missing. Please set it in fastapi_demo/.env.")

    client = Client()
    _ensure_langsmith_dataset(
        client,
        args.langsmith_dataset,
        overwrite=args.overwrite,
        source_dataset=args.hf_dataset,
        split_name=split_name,
    )
    created_count, skipped_existing_count, upload_failures = _upload_examples(
        client,
        args.langsmith_dataset,
        converted_examples,
    )
    print(
        "[Upload] created="
        f"{created_count} skipped_existing={skipped_existing_count} "
        f"failed={sum(upload_failures.values())} "
        f"failure_reasons={dict(upload_failures)}"
    )


if __name__ == "__main__":
    main()

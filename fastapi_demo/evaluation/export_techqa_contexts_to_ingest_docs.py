"""Export HF TechQA contexts into ingest_docs for startup vector ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from datasets import Dataset, DatasetDict, load_dataset
from dotenv import load_dotenv

DEFAULT_HF_DATASET = "nvidia/TechQA-RAG-Eval"
DEFAULT_HF_SPLIT = "test"
DEFAULT_OUTPUT_DIR = "ingest_docs"


def _short_sha1(text: str, length: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def _safe_filename(filename: str) -> str:
    raw = str(filename or "").strip().replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    if not safe:
        return ""
    if "." not in safe:
        safe = f"{safe}.txt"
    return safe


def _resolve_split(dataset_dict: DatasetDict, requested_split: str) -> str:
    if requested_split in dataset_dict:
        return requested_split
    for candidate in ("test", "validation", "train"):
        if candidate in dataset_dict:
            print(
                f"[WARN] Requested split '{requested_split}' not found. "
                f"Falling back to '{candidate}'."
            )
            return candidate
    fallback = next(iter(dataset_dict.keys()))
    print(
        f"[WARN] Requested split '{requested_split}' not found. "
        f"Falling back to '{fallback}'."
    )
    return fallback


def _resolve_conflict_name(base_name: str, text_hash: str, existing: dict[str, str]) -> str:
    path = Path(base_name)
    stem = path.stem or "context"
    suffix = path.suffix or ".txt"
    candidate = f"{stem}__{text_hash[:8]}{suffix}"
    index = 2
    while candidate in existing:
        candidate = f"{stem}__{text_hash[:8]}_{index}{suffix}"
        index += 1
    return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export contexts from HF TechQA dataset to ingest_docs.",
    )
    parser.add_argument("--hf-dataset", default=DEFAULT_HF_DATASET)
    parser.add_argument("--hf-split", default=DEFAULT_HF_SPLIT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Remove existing .txt files in output dir before export",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing files when names collide",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.clean_output:
        removed = 0
        for file_path in output_dir.glob("*.txt"):
            file_path.unlink(missing_ok=True)
            removed += 1
        print(f"[Output] clean_output removed_txt_files={removed}")

    dataset_dict = load_dataset(args.hf_dataset)
    split_name = _resolve_split(dataset_dict, args.hf_split)
    split_ds: Dataset = dataset_dict[split_name]

    print(
        f"[Load] hf_dataset={args.hf_dataset} requested_split={args.hf_split} "
        f"using_split={split_name} rows={len(split_ds)}"
    )

    stats = Counter()
    text_hash_by_name: dict[str, str] = {}
    text_by_name: dict[str, str] = {}
    first_source_by_name: dict[str, str] = {}

    for row_index, row in enumerate(split_ds):
        source_id = str(row.get("id") or f"{split_name}_{row_index:06d}")
        contexts = row.get("contexts") or []
        if not isinstance(contexts, list):
            stats["contexts_not_list"] += 1
            continue

        for context_index, context in enumerate(contexts):
            stats["contexts_total"] += 1
            if not isinstance(context, dict):
                stats["contexts_not_dict"] += 1
                continue

            text = str(context.get("text") or "").strip()
            if not text:
                stats["contexts_empty_text"] += 1
                continue

            stats["contexts_non_empty_text"] += 1
            raw_filename = str(context.get("filename") or "").strip()
            filename = _safe_filename(raw_filename)
            if not filename:
                filename = f"context_{_short_sha1(f'{source_id}_{context_index}_{text[:120]}')}.txt"
                stats["filename_missing_generated"] += 1

            text_hash = _short_sha1(text, length=40)
            target_name = filename
            if target_name in text_hash_by_name:
                if text_hash_by_name[target_name] == text_hash:
                    stats["duplicate_same_filename_same_text"] += 1
                    continue
                target_name = _resolve_conflict_name(filename, text_hash, text_hash_by_name)
                stats["duplicate_filename_conflict_renamed"] += 1

            text_hash_by_name[target_name] = text_hash
            text_by_name[target_name] = text
            first_source_by_name[target_name] = source_id

    write_stats = Counter()
    for file_name in sorted(text_by_name.keys()):
        text = text_by_name[file_name]
        target_path = output_dir / file_name
        if target_path.exists() and not args.overwrite_existing:
            existing = target_path.read_text(encoding="utf-8", errors="ignore")
            if existing.strip() == text.strip():
                write_stats["existing_same_skipped"] += 1
            else:
                write_stats["existing_different_skipped"] += 1
            continue
        target_path.write_text(text, encoding="utf-8")
        write_stats["written_files"] += 1

    manifest = {
        "hf_dataset": args.hf_dataset,
        "requested_split": args.hf_split,
        "used_split": split_name,
        "output_dir": str(output_dir),
        "stats": {
            **dict(stats),
            **dict(write_stats),
            "unique_export_files": len(text_by_name),
        },
        "examples": [
            {
                "file_name": name,
                "source_id": first_source_by_name[name],
                "text_hash_sha1": text_hash_by_name[name],
            }
            for name in sorted(text_by_name.keys())[:20]
        ],
    }
    manifest_path = output_dir / "_techqa_context_export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Output] output_dir={output_dir}")
    print(f"[Output] manifest={manifest_path}")
    print(f"[Stats] {json.dumps(manifest['stats'], ensure_ascii=False)}")
    print("[Preview] first_files=", sorted(text_by_name.keys())[:5])


if __name__ == "__main__":
    main()

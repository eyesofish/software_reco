"""Direct retrieval benchmark harness for bucketed LangSmith datasets.

Usage examples:
  python -m evaluation.retrieval_benchmark --dataset rag_retrieval_eval_b0_100 --method vector_only
  python -m evaluation.retrieval_benchmark --dataset rag_retrieval_eval_b1_100 --method hybrid_rerank --output-json evaluation/reports/raw.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from dotenv import load_dotenv


DEFAULT_TOP_K = 10

METHOD_LABELS: dict[str, str] = {
    "bm25_only": "BM25-only",
    "vector_only": "Vector-only",
    "hybrid_no_rerank": "BM25 + Vector",
    "hybrid_rerank": "BM25 + Vector + Rerank",
    "vector_child_only": "Vector-only + child-only + no-rerank",
    "vector_parent_child": "Vector-only + parent-child + no-rerank",
}

METHOD_PRESETS: dict[str, dict[str, str]] = {
    "bm25_only": {
        "RECALL_ENABLE_KEYWORD": "true",
        "RECALL_ENABLE_VECTOR": "false",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "false",
        "ENABLE_PARENT_CHILD_CHUNKING": "true",
    },
    "vector_only": {
        "RECALL_ENABLE_KEYWORD": "false",
        "RECALL_ENABLE_VECTOR": "true",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "false",
        "ENABLE_PARENT_CHILD_CHUNKING": "true",
    },
    "hybrid_no_rerank": {
        "RECALL_ENABLE_KEYWORD": "true",
        "RECALL_ENABLE_VECTOR": "true",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "false",
        "ENABLE_PARENT_CHILD_CHUNKING": "true",
    },
    "hybrid_rerank": {
        "RECALL_ENABLE_KEYWORD": "true",
        "RECALL_ENABLE_VECTOR": "true",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "true",
        "ENABLE_PARENT_CHILD_CHUNKING": "true",
    },
    "vector_child_only": {
        "RECALL_ENABLE_KEYWORD": "false",
        "RECALL_ENABLE_VECTOR": "true",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "false",
        "ENABLE_PARENT_CHILD_CHUNKING": "false",
    },
    "vector_parent_child": {
        "RECALL_ENABLE_KEYWORD": "false",
        "RECALL_ENABLE_VECTOR": "true",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RETRIEVAL_ENABLE_RERANK": "false",
        "ENABLE_PARENT_CHILD_CHUNKING": "true",
    },
}


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _bool_to_env(value: bool) -> str:
    return "true" if value else "false"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _as_unique_str_list(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        output.append(text)
        seen.add(text)
    return output


def _resolve_path(value: str | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _doc_metadata(doc: Any) -> dict[str, Any]:
    metadata = _get_value(doc, "metadata", {}) or {}
    if isinstance(metadata, dict):
        return metadata
    if hasattr(metadata, "model_dump"):
        return dict(metadata.model_dump())
    if hasattr(metadata, "dict"):
        return dict(metadata.dict())
    return {
        "doc_id": _get_value(metadata, "doc_id"),
        "source": _get_value(metadata, "source"),
        "channel": _get_value(metadata, "channel"),
        "retrieval_source": _get_value(metadata, "retrieval_source"),
        "score": _get_value(metadata, "score"),
        "url": _get_value(metadata, "url"),
    }


def _resolved_doc_id(doc: Any) -> str:
    metadata = _doc_metadata(doc)
    candidate = metadata.get("doc_id") or metadata.get("source_doc_id") or metadata.get("url")
    return str(candidate or "").strip()


def _dcg(binary_relevances: Sequence[int]) -> float:
    total = 0.0
    for rank, relevance in enumerate(binary_relevances, start=1):
        if relevance <= 0:
            continue
        total += float(relevance) / math.log2(rank + 1)
    return total


def compute_retrieval_metrics(
    gold_doc_ids: Sequence[str],
    retrieved_doc_ids: Sequence[str],
) -> dict[str, float]:
    gold = _as_unique_str_list(gold_doc_ids)
    retrieved = _as_unique_str_list(retrieved_doc_ids)
    if not gold:
        raise ValueError("gold_doc_ids must be non-empty for metric computation.")

    gold_set = set(gold)
    top5 = retrieved[:5]
    top10 = retrieved[:10]
    hit5 = [doc_id for doc_id in top5 if doc_id in gold_set]
    hit10 = [doc_id for doc_id in top10 if doc_id in gold_set]
    ndcg_binary = [1 if doc_id in gold_set else 0 for doc_id in top10]
    ideal_hits = min(len(gold_set), 10)
    ideal_binary = [1] * ideal_hits
    ideal_binary.extend([0] * max(0, 10 - ideal_hits))
    ideal_dcg = _dcg(ideal_binary[:10])
    ndcg = _dcg(ndcg_binary) / ideal_dcg if ideal_dcg > 0 else 0.0

    return {
        "recall_at_5": len(hit5) / len(gold_set),
        "recall_at_10": len(hit10) / len(gold_set),
        "hit_at_5": 1.0 if hit5 else 0.0,
        "hit_at_10": 1.0 if hit10 else 0.0,
        "ndcg_at_10": ndcg,
    }


def _mean(values: Iterable[float]) -> float | None:
    numbers = [float(value) for value in values]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _apply_method_preset(method: str, top_k: int) -> dict[str, str]:
    if method not in METHOD_PRESETS:
        raise ValueError(f"Unknown method preset: {method}")

    common_env = {
        "LANGSMITH_TRACING": "false",
        "RECALL_ENABLE_WEB": "false",
        "RECALL_ENABLE_MEMORY": "false",
        "RERANK_FINAL_TOP_N": str(max(DEFAULT_TOP_K, int(top_k))),
    }
    effective = dict(common_env)
    effective.update(METHOD_PRESETS[method])
    for key, value in effective.items():
        os.environ[key] = str(value)
    return effective


def _load_examples(dataset_name: str) -> list[dict[str, Any]]:
    from langsmith import Client

    client = Client()
    rows: list[dict[str, Any]] = []
    for example in client.list_examples(dataset_name=dataset_name):
        inputs = _get_value(example, "inputs", {}) or {}
        outputs = _get_value(example, "outputs", {}) or {}
        metadata = _get_value(example, "metadata", {}) or {}
        source_id = str(_get_value(metadata, "source_id", "") or _get_value(example, "id", "")).strip()
        rows.append(
            {
                "example_id": str(_get_value(example, "id", "") or "").strip(),
                "source_id": source_id,
                "question": str(_get_value(inputs, "question", "") or "").strip(),
                "gold_doc_ids": _as_unique_str_list(_get_value(outputs, "gold_doc_ids")),
                "metadata": metadata,
            }
        )
    rows.sort(key=lambda item: (item["source_id"], item["example_id"]))
    return rows


def _runtime_summary(settings: Any) -> dict[str, Any]:
    import chromadb

    chroma_path = _resolve_path(getattr(settings, "CHROMA_DB_PATH", ""))
    client = chromadb.PersistentClient(path=str(chroma_path)) if chroma_path else None

    def collection_count(name: str) -> int:
        if client is None:
            return 0
        try:
            return int(client.get_collection(name).count())
        except Exception:
            return 0

    child_name = "software_recommendations"
    parent_name = str(getattr(settings, "PARENT_COLLECTION_NAME", "software_recommendations_parent"))
    payload = {
        "chroma_db_path": str(chroma_path) if chroma_path else "",
        "ingest_path": str(_resolve_path(os.environ.get("INGEST_PATH", "")) or ""),
        "enable_parent_child_chunking": bool(getattr(settings, "ENABLE_PARENT_CHILD_CHUNKING", False)),
        "recall_enable_keyword": bool(getattr(settings, "RECALL_ENABLE_KEYWORD", False)),
        "recall_enable_vector": bool(getattr(settings, "RECALL_ENABLE_VECTOR", False)),
        "recall_enable_web": bool(getattr(settings, "RECALL_ENABLE_WEB", False)),
        "recall_enable_memory": bool(getattr(settings, "RECALL_ENABLE_MEMORY", False)),
        "retrieval_enable_rerank": bool(getattr(settings, "RETRIEVAL_ENABLE_RERANK", False)),
        "rerank_final_top_n": int(getattr(settings, "RERANK_FINAL_TOP_N", DEFAULT_TOP_K)),
        "child_collection_name": child_name,
        "parent_collection_name": parent_name,
        "child_collection_count": collection_count(child_name),
        "parent_collection_count": collection_count(parent_name),
    }
    if client is not None:
        payload["collections"] = sorted(getattr(item, "name", str(item)) for item in client.list_collections())
    else:
        payload["collections"] = []
    return payload


def _write_csv(output_path: Path, rows: Sequence[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "example_id",
                "source_id",
                "eligible",
                "question",
                "gold_doc_ids",
                "retrieved_doc_ids_top10",
                "recall_at_5",
                "recall_at_10",
                "hit_at_5",
                "hit_at_10",
                "ndcg_at_10",
                "skip_reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "example_id": row["example_id"],
                    "source_id": row["source_id"],
                    "eligible": _bool_to_env(bool(row["eligible"])),
                    "question": row["question"],
                    "gold_doc_ids": "|".join(row["gold_doc_ids"]),
                    "retrieved_doc_ids_top10": "|".join(row["retrieved_doc_ids"][:10]),
                    "recall_at_5": row["metrics"].get("recall_at_5"),
                    "recall_at_10": row["metrics"].get("recall_at_10"),
                    "hit_at_5": row["metrics"].get("hit_at_5"),
                    "hit_at_10": row["metrics"].get("hit_at_10"),
                    "ndcg_at_10": row["metrics"].get("ndcg_at_10"),
                    "skip_reason": row.get("skip_reason", ""),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run direct retrieval benchmarking over a LangSmith dataset.")
    parser.add_argument("--dataset", required=True, help="LangSmith dataset name")
    parser.add_argument(
        "--method",
        required=True,
        choices=sorted(METHOD_PRESETS.keys()),
        help="Named retrieval method preset",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Evaluate ranked retrieval up to top-k (must be >= 10)",
    )
    parser.add_argument("--output-json", default="", help="Write machine-readable JSON results")
    parser.add_argument("--output-csv", default="", help="Write optional per-example CSV results")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_k < DEFAULT_TOP_K:
        raise ValueError("--top-k must be >= 10 for the required benchmark metrics.")

    root_dir = Path(__file__).resolve().parents[1]
    load_dotenv(root_dir / ".env")
    preset_env = _apply_method_preset(args.method, args.top_k)

    import software_recommend_system.observability as observability

    observability._langsmith_traceable = None
    observability._langsmith_wrap_openai = None

    from software_recommend_system.config import settings
    from software_recommend_system.retriever import retrieve

    runtime = _runtime_summary(settings)
    if runtime["recall_enable_web"] or runtime["recall_enable_memory"]:
        raise RuntimeError("Retrieval benchmark requires RECALL_ENABLE_WEB=false and RECALL_ENABLE_MEMORY=false.")

    examples = _load_examples(args.dataset)
    per_example: list[dict[str, Any]] = []
    metric_rows: list[dict[str, float]] = []

    for row in examples:
        question = row["question"]
        gold_doc_ids = _as_unique_str_list(row["gold_doc_ids"])
        if not question:
            per_example.append(
                {
                    "example_id": row["example_id"],
                    "source_id": row["source_id"],
                    "question": question,
                    "gold_doc_ids": gold_doc_ids,
                    "retrieved_doc_ids": [],
                    "retrieved_docs": [],
                    "metrics": {},
                    "eligible": False,
                    "skip_reason": "missing_question",
                }
            )
            continue
        if not gold_doc_ids:
            per_example.append(
                {
                    "example_id": row["example_id"],
                    "source_id": row["source_id"],
                    "question": question,
                    "gold_doc_ids": gold_doc_ids,
                    "retrieved_doc_ids": [],
                    "retrieved_docs": [],
                    "metrics": {},
                    "eligible": False,
                    "skip_reason": "missing_gold_doc_ids",
                }
            )
            continue

        docs = retrieve(query=question, top_k=args.top_k)
        ranked_doc_ids: list[str] = []
        ranked_docs: list[dict[str, Any]] = []
        for rank, doc in enumerate(docs[: args.top_k], start=1):
            metadata = _doc_metadata(doc)
            doc_id = _resolved_doc_id(doc)
            ranked_doc_ids.append(doc_id)
            ranked_docs.append(
                {
                    "rank": rank,
                    "doc_id": doc_id,
                    "source": str(metadata.get("source", "") or ""),
                    "channel": str(metadata.get("channel", "") or metadata.get("retrieval_source", "") or ""),
                    "score": float(_get_value(doc, "score", 0.0) or 0.0),
                }
            )

        metrics = compute_retrieval_metrics(gold_doc_ids=gold_doc_ids, retrieved_doc_ids=ranked_doc_ids)
        metric_rows.append(metrics)
        per_example.append(
            {
                "example_id": row["example_id"],
                "source_id": row["source_id"],
                "question": question,
                "gold_doc_ids": gold_doc_ids,
                "retrieved_doc_ids": ranked_doc_ids,
                "retrieved_docs": ranked_docs,
                "metrics": metrics,
                "eligible": True,
                "skip_reason": "",
            }
        )

    aggregate = {
        "recall_at_5": _mean(item["recall_at_5"] for item in metric_rows),
        "recall_at_10": _mean(item["recall_at_10"] for item in metric_rows),
        "hit_at_5": _mean(item["hit_at_5"] for item in metric_rows),
        "hit_at_10": _mean(item["hit_at_10"] for item in metric_rows),
        "ndcg_at_10": _mean(item["ndcg_at_10"] for item in metric_rows),
    }

    payload = {
        "dataset": args.dataset,
        "method": args.method,
        "method_label": METHOD_LABELS[args.method],
        "top_k": int(args.top_k),
        "counts": {
            "total_examples": len(examples),
            "eligible_examples": len(metric_rows),
            "skipped_examples": len(examples) - len(metric_rows),
            "skipped_missing_question": sum(1 for row in per_example if row.get("skip_reason") == "missing_question"),
            "skipped_missing_gold_doc_ids": sum(
                1 for row in per_example if row.get("skip_reason") == "missing_gold_doc_ids"
            ),
        },
        "aggregate": aggregate,
        "effective_env": preset_env,
        "runtime": runtime,
        "per_example": per_example,
    }

    output_json = _resolve_path(args.output_json)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    output_csv = _resolve_path(args.output_csv)
    if output_csv is not None:
        _write_csv(output_csv, per_example)

    print(f"METHOD={args.method}")
    print(f"METHOD_LABEL={METHOD_LABELS[args.method]}")
    print(f"DATASET={args.dataset}")
    print(f"TOTAL_EXAMPLES={payload['counts']['total_examples']}")
    print(f"ELIGIBLE_EXAMPLES={payload['counts']['eligible_examples']}")
    print(f"RECALL_AT_5={aggregate['recall_at_5']}")
    print(f"RECALL_AT_10={aggregate['recall_at_10']}")
    print(f"HIT_AT_5={aggregate['hit_at_5']}")
    print(f"HIT_AT_10={aggregate['hit_at_10']}")
    print(f"NDCG_AT_10={aggregate['ndcg_at_10']}")
    if output_json is not None:
        print(f"RESULT_JSON={output_json}")
    if output_csv is not None:
        print(f"RESULT_CSV={output_csv}")


if __name__ == "__main__":
    main()

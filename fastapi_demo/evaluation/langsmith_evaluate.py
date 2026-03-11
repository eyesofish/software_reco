"""Minimal LangSmith evaluation harness for the existing RAG workflow.

Usage examples:
  python -m evaluation.langsmith_evaluate --dataset rag_hitl_eval_v1 --policy auto_confirm
  python -m evaluation.langsmith_evaluate --dataset rag_hitl_eval_v1 --policy oracle_edit
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, Optional, Set
from uuid import uuid4

import openai
from dotenv import load_dotenv
from langsmith import Client, evaluate
from langsmith.evaluation import EvaluationResult
from langsmith.schemas import Example, Run

from software_recommend_system.observability import traceable, wrap_openai
from software_recommend_system.state import AgentState


load_dotenv()

JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "qwen2.5-7b-instruct")
AGENT = None
JUDGE_CLIENT: Optional[openai.OpenAI] = None


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_set(value: Any) -> Set[str]:
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value if str(v).strip()}
    return {str(value)}


def _judge_client() -> Optional[openai.OpenAI]:
    global JUDGE_CLIENT
    if JUDGE_CLIENT is not None:
        return JUDGE_CLIENT

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("OPENAI_BASE_URL") or None
    JUDGE_CLIENT = wrap_openai(openai.OpenAI(api_key=api_key, base_url=base_url))
    return JUDGE_CLIENT


def _agent():
    global AGENT
    if AGENT is None:
        from software_recommend_system.rag_agent import create_rag_with_routing_agent

        AGENT = create_rag_with_routing_agent()
    return AGENT


def _run_pipeline(question: str, hitl_policy: str, oracle_edits: Optional[list[str]] = None) -> Dict[str, Any]:
    session_id = f"eval-{uuid4().hex}"
    state = AgentState(
        user_query=question,
        timeout_budget=60,
        max_iterations=3,
        start_time=time.time(),
        session_id=session_id,
        hitl_policy=hitl_policy,
        oracle_edits=oracle_edits or [],
    )
    result = _agent().invoke(state, config={"configurable": {"thread_id": session_id}})

    return {
        "final_answer": _get_value(result, "final_answer", ""),
        "mode": _get_value(result, "mode", ""),
        "hitl": _get_value(result, "hitl", {}),
        "retrieval_records": _get_value(result, "retrieval_records", []),
        "retrieved_doc_ids": _get_value(result, "retrieved_doc_ids", []),
        "retrieved_doc_ids_full": _get_value(result, "retrieved_doc_ids_full", []),
    }


@traceable(name="eval_target_auto_confirm")
def run_pipeline_auto_confirm(inputs: Dict[str, Any]) -> Dict[str, Any]:
    question = str(inputs.get("question", "")).strip()
    return _run_pipeline(question=question, hitl_policy="auto_confirm")


@traceable(name="eval_target_oracle_edit")
def run_pipeline_oracle_edit(inputs: Dict[str, Any]) -> Dict[str, Any]:
    question = str(inputs.get("question", "")).strip()
    oracle_edits_raw = inputs.get("oracle_edits", [])
    oracle_edits = [str(v).strip() for v in (oracle_edits_raw or []) if str(v).strip()]
    return _run_pipeline(
        question=question,
        hitl_policy="oracle_edit",
        oracle_edits=oracle_edits,
    )


def routing_accuracy(run: Run, example: Example) -> EvaluationResult:
    expected = (
        _get_value(_get_value(example, "outputs", {}), "expected_mode")
        or _get_value(_get_value(example, "metadata", {}), "expected_mode")
    )
    predicted = _get_value(_get_value(run, "outputs", {}), "mode")

    if not expected:
        return EvaluationResult(
            key="routing_accuracy",
            score=None,
            comment="No expected_mode in dataset row.",
        )
    score = 1.0 if str(predicted) == str(expected) else 0.0
    return EvaluationResult(
        key="routing_accuracy",
        score=score,
        comment=f"expected={expected}, predicted={predicted}",
    )


def retrieval_recall(run: Run, example: Example) -> EvaluationResult:
    outputs = _get_value(run, "outputs", {}) or {}
    retrieved_full = _as_set(_get_value(outputs, "retrieved_doc_ids_full"))
    retrieved_top = _as_set(_get_value(outputs, "retrieved_doc_ids"))
    retrieved = retrieved_full or retrieved_top
    gold = _as_set(_get_value(_get_value(example, "outputs", {}), "gold_doc_ids"))

    if not gold:
        return EvaluationResult(
            key="retrieval_recall",
            score=None,
            comment="No gold_doc_ids in dataset row.",
        )

    hit = retrieved & gold
    score = len(hit) / len(gold)
    return EvaluationResult(
        key="retrieval_recall",
        score=score,
        metadata={
            "retrieved_count": len(retrieved),
            "retrieved_source": "retrieved_doc_ids_full" if retrieved_full else "retrieved_doc_ids",
            "gold_count": len(gold),
            "hit_count": len(hit),
        },
    )


def answer_correctness(run: Run, example: Example) -> EvaluationResult:
    question = str(_get_value(_get_value(example, "inputs", {}), "question", ""))
    prediction = str(_get_value(_get_value(run, "outputs", {}), "final_answer", ""))
    reference = str(_get_value(_get_value(example, "outputs", {}), "reference_answer", ""))

    if not reference:
        return EvaluationResult(
            key="answer_correctness",
            score=None,
            comment="No reference_answer in dataset row.",
        )

    client = _judge_client()
    if client is None:
        return EvaluationResult(
            key="answer_correctness",
            score=None,
            comment="Missing OPENAI_API_KEY/DASHSCOPE_API_KEY for judge model.",
        )

    prompt = (
        "You are grading answer correctness. Return strict JSON: "
        '{"score": <float 0..1>, "reason": "..."}.\n\n'
        f"Question:\n{question}\n\n"
        f"Reference Answer:\n{reference}\n\n"
        f"Model Answer:\n{prediction}\n"
    )

    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = _get_value(_get_value(_get_value(resp, "choices", [{}])[0], "message", {}), "content", "")
        parsed = json.loads(content or "{}")
        score = float(parsed.get("score", 0.0))
        reason = str(parsed.get("reason", ""))
    except Exception as exc:
        return EvaluationResult(
            key="answer_correctness",
            score=0.0,
            comment=f"Judge invocation failed: {exc}",
        )

    score = max(0.0, min(1.0, score))
    return EvaluationResult(key="answer_correctness", score=score, comment=reason)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LangSmith evaluation for this RAG system.")
    parser.add_argument("--dataset", required=True, help="LangSmith dataset name")
    parser.add_argument(
        "--policy",
        choices=["auto_confirm", "oracle_edit"],
        default="auto_confirm",
        help="HITL policy for target run",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Experiment prefix (optional, auto-generated if omitted)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="Evaluate max concurrency",
    )
    args = parser.parse_args()

    target = run_pipeline_auto_confirm if args.policy == "auto_confirm" else run_pipeline_oracle_edit
    prefix = args.prefix or f"rag-hitl-{args.policy}"
    client = Client()
    _ = client  # keep explicit client creation for env validation

    evaluate(
        target,
        data=args.dataset,
        evaluators=[routing_accuracy, retrieval_recall, answer_correctness],
        experiment_prefix=prefix,
        metadata={"hitl_policy": args.policy, "pipeline": "rag+chat"},
        max_concurrency=args.max_concurrency,
    )


if __name__ == "__main__":
    main()

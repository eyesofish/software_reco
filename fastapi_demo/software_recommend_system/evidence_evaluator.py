"""Evidence quality evaluation.

`quality_score` used to be `avg(doc.score) + 0.1`, a placeholder whose own
comment admitted it. It now feeds the coverage gate in `coverage_check_node`,
so a meaningless value directly distorts the retrieval refinement loop.

Two strategies are provided:

* :func:`heuristic_quality_score` — deterministic and offline. Blends the
  reranked retrieval score with question/document lexical overlap and a
  substance term so a near-empty snippet cannot score like a real passage.
* :func:`llm_quality_scores` — an LLM judge that rates how well each document
  actually supports the question. Opt-in via ``EVIDENCE_EVAL_USE_LLM``.

The LLM path is strictly an enhancement: any failure, malformed payload or
length mismatch falls back to the heuristic, so the pipeline keeps working
offline and without an API key.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import settings
from .llm_governance import governed_chat_completion
from .logging_utils import log_event

logger = logging.getLogger(__name__)

_RETRIEVAL_WEIGHT = 0.55
_OVERLAP_WEIGHT = 0.30
_SUBSTANCE_WEIGHT = 0.15
_SUBSTANCE_TARGET_CHARS = 240

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")

_JUDGE_SYSTEM_PROMPT = (
    "You grade retrieved evidence for a technical question. "
    "For each numbered document, judge how well it supports answering the question. "
    "Use 1.0 for directly answering, 0.5 for partially relevant background, "
    "0.0 for unrelated. Reply with JSON only, no prose, in the form "
    '{"scores": [{"index": 1, "support": 0.0}]}. '
    "Return exactly one entry per document, preserving the given indexes."
)


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _item_documents(item: Any) -> list[Any]:
    return list(_get_field(item, "documents", []) or [])


def _item_content(item: Any) -> str:
    parts = [str(_get_field(doc, "content", "") or "") for doc in _item_documents(item)]
    return "\n".join(part for part in parts if part).strip()


def heuristic_quality_score(question: str, item: Any) -> float:
    """Deterministic evidence score in [0, 1].

    Replaces the old `avg + 0.1`, which could never drop below 0.1 and ignored
    whether the document had anything to do with the question.
    """
    docs = _item_documents(item)
    if not docs:
        return 0.0

    scores = [_clamp01(_safe_float(_get_field(doc, "score", 0.0), 0.0)) for doc in docs]
    retrieval = sum(scores) / len(scores) if scores else 0.0

    content = _item_content(item)
    question_tokens = _tokenize(question)
    overlap = 0.0
    if question_tokens:
        content_tokens = _tokenize(content)
        overlap = len(question_tokens & content_tokens) / len(question_tokens)

    substance = min(1.0, len(content) / _SUBSTANCE_TARGET_CHARS) if content else 0.0

    return _clamp01(
        _RETRIEVAL_WEIGHT * retrieval
        + _OVERLAP_WEIGHT * _clamp01(overlap)
        + _SUBSTANCE_WEIGHT * substance
    )


def _judge_model() -> str:
    return str(
        getattr(settings, "EVIDENCE_EVAL_MODEL", "") or getattr(settings, "LLM_MODEL", "")
    ).strip()


def _extract_text(response: Any) -> str:
    choices = _get_field(response, "choices", []) or []
    if not choices:
        return ""
    message = _get_field(choices[0], "message", None)
    content = _get_field(message, "content", "") if message is not None else ""
    if isinstance(content, list):
        return "".join(str(_get_field(part, "text", "") or "") for part in content)
    return str(content or "")


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_judge_messages(question: str, items: list[Any], content_chars: int) -> list[dict[str, str]]:
    blocks = []
    for index, item in enumerate(items, start=1):
        content = _item_content(item)[:content_chars]
        blocks.append(f"[{index}] {content}")
    user_content = f"Question: {question}\n\nDocuments:\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def llm_quality_scores(question: str, items: list[Any], *, client: Any | None = None) -> list[float] | None:
    """Return one support score per item, or ``None`` when the judge is unusable.

    ``None`` (rather than a partial list) is returned on any problem so callers
    fall back to the deterministic heuristic for the whole batch instead of
    mixing two incomparable scales.
    """
    if not items:
        return None

    model = _judge_model()
    if not model:
        return None

    max_docs = max(1, int(_safe_float(getattr(settings, "EVIDENCE_EVAL_MAX_DOCS", 8), 8)))
    if len(items) > max_docs:
        return None

    content_chars = max(80, int(_safe_float(getattr(settings, "EVIDENCE_EVAL_CONTENT_CHARS", 600), 600)))

    if client is None:
        from .llm_utils import _get_openai_client

        client = _get_openai_client()

    try:
        response = governed_chat_completion(
            client=client,
            scene="evidence_evaluation",
            model=model,
            messages=_build_judge_messages(question, items, content_chars),
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001  judge is best-effort
        log_event(
            logger,
            logging.WARNING,
            "rag.evidence.judge.fail",
            component="rag",
            model=model,
            items=len(items),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    parsed = _parse_json_object(_extract_text(response))
    raw_scores = parsed.get("scores")
    if not isinstance(raw_scores, list) or len(raw_scores) != len(items):
        log_event(
            logger,
            logging.WARNING,
            "rag.evidence.judge.invalid",
            component="rag",
            model=model,
            expected=len(items),
            received=len(raw_scores) if isinstance(raw_scores, list) else -1,
        )
        return None

    by_index: dict[int, float] = {}
    for entry in raw_scores:
        if not isinstance(entry, dict):
            return None
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            return None
        if not 1 <= index <= len(items) or index in by_index:
            return None
        by_index[index] = _clamp01(_safe_float(entry.get("support"), 0.0))

    if len(by_index) != len(items):
        return None
    return [by_index[i] for i in range(1, len(items) + 1)]


def evaluate_evidence(question: str, items: list[Any]) -> tuple[list[float], str]:
    """Score every evidence item. Returns ``(scores, mode)``.

    ``mode`` is ``"llm_judge"`` or ``"heuristic"`` so the caller can log which
    path produced the values.
    """
    if not items:
        return [], "heuristic"

    if bool(getattr(settings, "EVIDENCE_EVAL_USE_LLM", False)):
        judged = llm_quality_scores(question, items)
        if judged is not None:
            return judged, "llm_judge"

    return [heuristic_quality_score(question, item) for item in items], "heuristic"

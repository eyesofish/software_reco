"""Paired, decision-only routing evaluation. Recorded predictions are the safe default.

No graph/checkpointer/session store is imported by this module. Live classification
requires an explicit --candidate jev and/or --baseline legacy option.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.performance_metrics import distribution
from software_recommend_system.intent_routing import (
    RoutingSnapshot,
    policy_fingerprint,
    resolve_routing,
    routing_policy,
)
from software_recommend_system.routing_shadow import snapshot_hash

CASE_VERSION: Final = "routing-case.v1"
VERIFIED_ANNOTATIONS = {"human_review", "explicit_user_choice_verified"}


class RoutingCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["routing-case.v1"] = CASE_VERSION
    case_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    occurred_at: datetime
    context_as_of: datetime
    split: Literal["calibration", "holdout"] = "holdout"
    cohort: Literal["representative", "challenge"] = "representative"
    annotation_source: str | None = None
    snapshot: RoutingSnapshot
    expected: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check_time(self) -> RoutingCase:
        if self.occurred_at.tzinfo is None or self.context_as_of.tzinfo is None:
            raise ValueError("timestamps require explicit timezone")
        if self.context_as_of > self.occurred_at:
            raise ValueError("future_context_snapshot")
        if self.expected.get("target") == "existing" and not self.expected.get("target_session_id"):
            raise ValueError("existing_truth_requires_session_id")
        if self.expected.get("target") not in {None, "existing", "new", "clarify"}:
            raise ValueError("invalid_target_truth")
        for message in self.snapshot.context.get("messages", []):
            if isinstance(message, dict) and message.get("occurred_at"):
                when = datetime.fromisoformat(str(message["occurred_at"]).replace("Z", "+00:00"))
                if when.tzinfo is None or when > self.occurred_at:
                    raise ValueError("future_context_message")
        return self


def load_cases(path: Path) -> list[RoutingCase]:
    cases = []
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                row.setdefault("snapshot", {}).setdefault("snapshot_id", row.get("case_id", ""))
                cases.append(RoutingCase.model_validate(row))
    validate_split(cases)
    return cases


def validate_split(cases: list[RoutingCase]) -> None:
    ids: set[str] = set()
    groups: dict[str, str] = {}
    for case in cases:
        if case.case_id in ids:
            raise ValueError("duplicate_case_id")
        ids.add(case.case_id)
        if case.group_id in groups and groups[case.group_id] != case.split:
            raise ValueError("conversation_crosses_calibration_and_holdout")
        groups[case.group_id] = case.split
    calibration = [case.occurred_at for case in cases if case.split == "calibration"]
    holdout = [case.occurred_at for case in cases if case.split == "holdout"]
    if calibration and holdout and max(calibration) > min(holdout):
        raise ValueError("calibration_must_precede_holdout")


def binomial_upper(errors: int, n: int, alpha: float = 0.05) -> float:
    """Exact one-sided Clopper-Pearson bound, with no SciPy dependency."""
    if not 0 <= errors <= n or not 0 < alpha < 1:
        raise ValueError("invalid_binomial_sample")
    if n == 0 or errors == n:
        return 1.0
    if errors == 0:
        return -math.expm1(math.log(alpha) / n)

    def cdf(p: float) -> float:
        log_term = n * math.log1p(-p)
        log_sum = log_term
        for i in range(1, errors + 1):
            log_term += math.log(n - i + 1) - math.log(i) + math.log(p) - math.log1p(-p)
            top = max(log_sum, log_term)
            log_sum = top + math.log1p(math.exp(-abs(log_sum - log_term)))
        return math.exp(log_sum)

    low, high = errors / n, 1.0 - 1e-15
    for _ in range(70):
        mid = (low + high) / 2
        if cdf(mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def session_risk_gate(errors: int, n: int, *, explicit_overrides: int = 0, limit: float = 0.001) -> dict[str, Any]:
    upper = binomial_upper(errors, n)
    return {
        "errors": errors,
        "n": n,
        "error_rate": errors / n if n else None,
        "one_sided_confidence": 0.95,
        "upper_bound": upper,
        "limit": limit,
        "explicit_id_overrides": explicit_overrides,
        "passed": n > 0 and upper <= limit and explicit_overrides == 0,
    }


def _prediction(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def evaluate_pairs(
    cases: list[RoutingCase],
    *,
    baseline: Callable[[dict[str, Any]], Any] | None = None,
    candidate: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    validate_split(cases)
    rows: list[dict[str, Any]] = []
    for case in cases:
        frozen = case.snapshot.model_dump(mode="json")
        row = {
            "case_id": case.case_id,
            "group_id": case.group_id,
            "occurred_at": case.occurred_at.isoformat(),
            "split": case.split,
            "cohort": case.cohort,
            "snapshot_hash": snapshot_hash(frozen),
            "binding": case.snapshot.binding,
            "strong_target": case.snapshot.target_session_id,
            "annotation_source": case.annotation_source,
            "expected": case.expected,
        }
        for name, runner in (("baseline", baseline), ("candidate", candidate)):
            try:
                value = runner(copy.deepcopy(frozen)) if runner else getattr(case, name)
                row[name] = _prediction(value) if value is not None else {"status": "missing", "source": "missing"}
            except Exception as exc:
                row[name] = {"status": "failed", "source": "fallback", "error_type": type(exc).__name__}
        rows.append(row)
    summary = {name: summarize_predictions(rows, name) for name in ("baseline", "candidate")}
    known_pairs = [
        row
        for row in rows
        if row["baseline"].get("status") not in {"missing", "failed"}
        and row["candidate"].get("status") not in {"missing", "failed"}
    ]
    different = sum(
        any(
            row["baseline"].get(key) != row["candidate"].get(key)
            for key in ("target", "target_session_id", "request_type", "mode", "next_action")
        )
        for row in known_pairs
    )
    gate = summary["candidate"]["session_risk_gate"]
    fingerprints = {
        row["candidate"].get("policy_fingerprint")
        for row in rows
        if row["binding"] == "unresolved" and row["candidate"].get("status") == "ready"
    }
    matching_policy = fingerprints == {policy_fingerprint()}
    promotion_gate = {
        "passed": gate["passed"] and matching_policy,
        "upper_bound": gate["upper_bound"],
        "risk_limit": 0.001,
        "confidence_level": 0.95,
        "n": gate["n"],
        "errors": gate["errors"],
        "taxonomy_version": routing_policy()["taxonomy_version"],
        "model_version": routing_policy()["model_version"],
        "policy_fingerprint": policy_fingerprint(),
        "policy_verified": matching_policy,
    }
    return {
        "schema_version": "routing-report.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "gate": promotion_gate,
        "candidate_policy": routing_policy(),
        "case_count": len(rows),
        "summary": summary,
        "rows": rows,
        "disagreement": {
            "n": len(known_pairs),
            "count": different,
            "rate": different / len(known_pairs) if known_pairs else None,
        },
        "limitations": [
            "Disagreement is not error; missing labels are never treated as correct.",
            "The risk gate uses one preselected eligible row per conversation group, not every turn.",
            "Decision-only replay does not establish full candidate execution latency or cost.",
        ],
    }


def _target_wrong(prediction: dict[str, Any], expected: dict[str, Any]) -> bool:
    return prediction.get("target") != expected.get("target") or (
        expected.get("target") == "existing"
        and prediction.get("target_session_id") != expected.get("target_session_id")
    )


def summarize_predictions(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    eligible = [row for row in rows if row["binding"] == "unresolved"]
    accepted = [row for row in eligible if row[name].get("status") == "ready"]
    labeled_existing = [
        row for row in accepted if row[name].get("target") == "existing" and row["expected"].get("target")
    ]
    existing_errors = sum(_target_wrong(row[name], row["expected"]) for row in labeled_existing)
    explicit_overrides = sum(
        row[name].get("status") == "ready"
        and (
            row[name].get("target") != row["binding"]
            or (row["binding"] == "existing" and row[name].get("target_session_id") != row["strong_target"])
        )
        for row in rows
        if row["binding"] != "unresolved"
    )
    # Preselect before reading predictions/truth to avoid cherry-picking favorable turns.
    independent: dict[str, dict[str, Any]] = {}
    for row in sorted(eligible, key=lambda item: (item["occurred_at"], item["case_id"])):
        if row["split"] == "holdout" and row["cohort"] == "representative":
            independent.setdefault(row["group_id"], row)
    gate_rows = [
        row
        for row in independent.values()
        if row[name].get("status") == "ready"
        and row[name].get("target") == "existing"
        and row["annotation_source"] in VERIFIED_ANNOTATIONS
        and row["expected"].get("target")
    ]
    gate_errors = sum(_target_wrong(row[name], row["expected"]) for row in gate_rows)
    decisions = [row[name] for row in rows]
    usage = [call for prediction in decisions for call in prediction.get("usage", [])]
    all_cost_known = bool(decisions) and all(
        prediction.get("usage") and all(call.get("cost_usd") is not None for call in prediction["usage"])
        for prediction in decisions
    )
    coverage = len(accepted) / len(eligible) if eligible else None
    dimensional = {}
    for field in ("task_relation", "request_type", "mode", "next_action"):
        labeled = [row for row in rows if row["expected"].get(field) is not None]
        correct = sum(row[name].get(field) == row["expected"][field] for row in labeled)
        dimensional[field] = {
            "n": len(labeled),
            "correct": correct,
            "accuracy": correct / len(labeled) if labeled else None,
        }
    return {
        "requests": len(rows),
        "semantic_eligible": len(eligible),
        "automatic_coverage": coverage,
        "jev_direct_accepts": sum(row[name].get("source") == "jev" for row in accepted),
        "reviewed_accepts": sum(row[name].get("source") == "llm" for row in accepted),
        "review_requests": sum(prediction.get("reviews_used", 0) > 0 for prediction in decisions),
        "technical_fallbacks": sum(prediction.get("source") == "fallback" for prediction in decisions),
        "clarifications": sum(prediction.get("status") == "routing_clarification" for prediction in decisions),
        "missing_or_failed": sum(prediction.get("status") in {"missing", "failed"} for prediction in decisions),
        "labeled_existing_accepts": len(labeled_existing),
        "wrong_existing_accepts": existing_errors,
        "wrong_existing_rate": existing_errors / len(labeled_existing) if labeled_existing else None,
        "wrong_new_accepts": sum(
            row[name].get("target") == "new"
            and bool(row["expected"].get("target"))
            and _target_wrong(row[name], row["expected"])
            for row in accepted
        ),
        "unlabeled_accepted": sum(not row["expected"].get("target") for row in accepted),
        "explicit_id_overrides": explicit_overrides,
        "latency_ms": distribution(
            [
                float(prediction["elapsed_ms"])
                for prediction in decisions
                if isinstance(prediction.get("elapsed_ms"), (int, float))
            ]
        ),
        "cost_usd": sum(call["cost_usd"] for call in usage) if all_cost_known else None,
        "usage_calls": len(usage),
        "unknown_cost_calls": sum(call.get("cost_usd") is None for call in usage),
        "unknown_usage_requests": sum(not prediction.get("usage") for prediction in decisions),
        "dimensions": dimensional,
        "session_risk_gate": session_risk_gate(gate_errors, len(gate_rows), explicit_overrides=explicit_overrides),
    }


def load_acceptance_gate(path: str | Path) -> bool:
    """Validate evidence and policy, not a hand-written 'passed' switch. Fail closed."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if report.get("schema_version") != "routing-report.v1":
            return False
        if report.get("candidate_policy") != routing_policy():
            return False
        gate = report.get("gate", {})
        if not gate.get("passed") or gate.get("policy_fingerprint") != policy_fingerprint():
            return False
        if gate.get("risk_limit") != 0.001 or gate.get("confidence_level") != 0.95:
            return False
        rows = report["rows"]
        if not rows or len({row["case_id"] for row in rows}) != len(rows):
            return False
        # Revalidate group/time segregation instead of trusting the summary numbers.
        group_splits: dict[str, str] = {}
        for row in rows:
            group_id = row["group_id"]
            if not group_id or (group_id in group_splits and group_splits[group_id] != row["split"]):
                return False
            group_splits[group_id] = row["split"]
            if (
                row["binding"] == "unresolved"
                and row["candidate"].get("status") == "ready"
                and row["candidate"].get("policy_fingerprint") != policy_fingerprint()
            ):
                return False
        checked = summarize_predictions(rows, "candidate")["session_risk_gate"]
        return (
            checked["passed"]
            and checked["n"] == gate["n"]
            and checked["errors"] == gate["errors"]
            and math.isclose(checked["upper_bound"], float(gate["upper_bound"]), rel_tol=1e-12)
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return False


def _legacy(snapshot: dict[str, Any]) -> dict[str, Any]:
    import time

    from software_recommend_system.llm_governance import usage_snapshot
    from software_recommend_system.performance import request_correlation
    from software_recommend_system.router import route_query

    started = time.monotonic()
    with request_correlation() as request_id:
        result = route_query(
            snapshot["query"], context_hint=json.dumps(snapshot.get("context", {}), ensure_ascii=False)
        )
        accounting = usage_snapshot(request_id=request_id)
    existing = snapshot["binding"] == "existing"
    return {
        "status": "ready",
        "target": "existing" if existing else "new",
        "target_session_id": snapshot.get("target_session_id") if existing else None,
        "task_relation": "continue" if existing else "new",
        "mode": result.mode,
        "source": "fallback" if result.fallback_used else "legacy",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "usage": accounting["records"]
        + ([{"cost_usd": None, "reason": "ledger_truncated"}] if accounting["records_truncated"] else []),
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "decisions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["case_id", "group_id", "snapshot_hash", "baseline", "candidate", "expected"]
        )
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow(
                {
                    key: json.dumps(row[key], ensure_ascii=False) if isinstance(row[key], dict) else row[key]
                    for key in writer.fieldnames
                }
            )
    lines = [
        "# Routing comparison",
        "",
        f"Cases: {report['case_count']}",
        "",
        "| Policy | Auto coverage | Wrong existing / labeled | 95% upper | Gate | P95 ms | Cost USD |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
    for name, summary in report["summary"].items():
        gate = summary["session_risk_gate"]
        passed = report["gate"]["passed"] if name == "candidate" else gate["passed"]
        cost = summary["cost_usd"] if summary["cost_usd"] is not None else "unknown"
        lines.append(
            f"| {name} | {summary['automatic_coverage']} | "
            f"{summary['wrong_existing_accepts']} / {summary['labeled_existing_accepts']} | "
            f"{gate['upper_bound']:.6f} | {'PASS' if passed else 'HOLD'} | "
            f"{summary['latency_ms']['p95']} | {cost} |"
        )
    lines.extend(["", *["- " + limitation for limitation in report["limitations"]]])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", choices=["recorded", "jev"], default="recorded")
    parser.add_argument("--baseline", choices=["recorded", "legacy"], default="recorded")
    parser.add_argument("--split", choices=["holdout", "calibration", "all"], default="holdout")
    args = parser.parse_args()
    cases = load_cases(args.input)
    selected = [case for case in cases if args.split == "all" or case.split == args.split]
    report = evaluate_pairs(
        selected,
        baseline=_legacy if args.baseline == "legacy" else None,
        candidate=resolve_routing if args.candidate == "jev" else None,
    )
    write_report(report, args.output)
    print(json.dumps({"report": str(args.output / "report.json"), "gate": report["gate"]}))


if __name__ == "__main__":
    main()

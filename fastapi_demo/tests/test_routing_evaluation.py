import json
from datetime import UTC, datetime, timedelta

import pytest

from evaluation.routing_evaluate import (
    RoutingCase,
    binomial_upper,
    evaluate_pairs,
    load_acceptance_gate,
    session_risk_gate,
    validate_split,
    write_report,
)
from software_recommend_system.intent_routing import policy_fingerprint


def case(number=0, **changes):
    data = {
        "case_id": f"case-{number}",
        "group_id": f"group-{number}",
        "occurred_at": datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=number),
        "context_as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "annotation_source": "human_review",
        "snapshot": {"snapshot_id": f"snap-{number}", "query": "继续", "current_conversation_id": "s1"},
        "expected": {"target": "existing", "target_session_id": "s1"},
        "candidate": {
            "status": "ready",
            "target": "existing",
            "target_session_id": "s1",
            "source": "jev",
            "elapsed_ms": 10,
            "policy_fingerprint": policy_fingerprint(),
        },
    }
    data.update(changes)
    return RoutingCase.model_validate(data)


def test_exact_zero_error_gate_requires_2995_independent_samples():
    assert not session_risk_gate(0, 2994)["passed"]
    assert session_risk_gate(0, 2995)["passed"]
    assert not session_risk_gate(0, 0)["passed"]
    assert not session_risk_gate(0, 2995, explicit_overrides=1)["passed"]
    assert binomial_upper(1, 100) > binomial_upper(0, 100)
    assert binomial_upper(100, 100) == 1


def test_pair_uses_deep_copies_without_future_or_cross_split_leakage():
    seen = []

    def baseline(snapshot):
        snapshot["query"] = "mutated"
        return {"status": "ready", "mode": "direct"}

    def candidate(snapshot):
        seen.append(snapshot["query"])
        return {"status": "ready", "mode": "rag"}

    sample = case()
    report = evaluate_pairs([sample], baseline=baseline, candidate=candidate)
    assert seen == ["继续"]
    assert sample.snapshot.query == "继续"
    assert report["disagreement"]["count"] == 1
    with pytest.raises(ValueError, match="future_context_snapshot"):
        case(context_as_of="2027-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="crosses"):
        validate_split([case(split="calibration"), case(1, group_id="group-0")])
    with pytest.raises(ValueError, match="precede"):
        validate_split([case(1, split="calibration"), case(0)])


def test_unlabeled_and_explicit_and_duplicate_groups_do_not_dilute_risk():
    report = evaluate_pairs(
        [
            case(0, expected={}),
            case(1, snapshot={"binding": "existing", "target_session_id": "s1"}),
            case(2),
            case(3, group_id="group-2"),
            case(4, cohort="challenge"),
            case(5, annotation_source="historical_assignment"),
        ]
    )
    summary = report["summary"]["candidate"]
    assert summary["session_risk_gate"]["n"] == 1
    assert summary["unlabeled_accepted"] == 1
    assert summary["dimensions"]["mode"]["accuracy"] is None
    assert summary["cost_usd"] is None


def test_repeated_messages_never_make_one_conversation_pass_gate():
    report = evaluate_pairs([case(i, group_id="same") for i in range(2995)])
    assert report["gate"]["n"] == 1
    assert not report["gate"]["passed"]


def test_wrong_existing_and_wrong_new_separate_from_accuracy():
    report = evaluate_pairs(
        [
            case(0, expected={"target": "new"}),
            case(1, candidate={"status": "ready", "target": "new", "source": "llm"}),
        ]
    )
    summary = report["summary"]["candidate"]
    assert summary["wrong_existing_accepts"] == 1
    assert summary["wrong_new_accepts"] == 1
    assert summary["automatic_coverage"] == 1


def test_gate_loader_recomputes_and_matches_current_policy(tmp_path):
    report = evaluate_pairs([case(i) for i in range(2995)])
    assert report["gate"]["passed"]
    write_report(report, tmp_path)
    path = tmp_path / "report.json"
    assert load_acceptance_gate(path)
    report["rows"][0]["expected"] = {"target": "new"}
    path.write_text(json.dumps(report), encoding="utf-8")
    assert not load_acceptance_gate(path)
    report["rows"][0]["expected"] = {"target": "existing", "target_session_id": "s1"}
    report["candidate_policy"]["model_version"] = "different-model"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert not load_acceptance_gate(path)


def test_unknown_request_usage_cannot_be_hidden_by_known_neighbor():
    priced = case(0)
    priced.candidate["usage"] = [{"cost_usd": 0.01}]
    report = evaluate_pairs([priced, case(1)])
    assert report["summary"]["candidate"]["cost_usd"] is None
    assert report["summary"]["candidate"]["unknown_usage_requests"] == 1

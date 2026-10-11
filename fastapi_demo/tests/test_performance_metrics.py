import json

import pytest

from evaluation.performance_metrics import (
    SSEParser,
    StreamObservation,
    compare_baseline,
    make_baseline,
    percentile,
    summarize,
)


def test_fragmented_utf8_crlf_and_multiline_data():
    parser = SSEParser()
    data = ('\ufeff: heartbeat\r\nevent: token\r\ndata: {"delta":\r\ndata: "你"}\r\n\r\n').encode()
    events = []
    for byte in data:
        events.extend(parser.feed(bytes([byte])))
    events.extend(parser.feed(b"", final=True))
    assert events == [("token", {"delta": "你"})]


def test_meta_empty_tokens_errors_and_missing_terminal():
    observation = StreamObservation()
    observation.accept("meta", {}, 1)
    observation.accept("node", {}, 2)
    observation.accept("token", {"delta": " "}, 3)
    assert observation.ttft_ms is None
    observation.accept("token", {"delta": "hello"}, 10)
    observation.accept("final", {"status": "success", "final_answer": "hello"}, 20)
    observation.validate(expect_token=True)
    assert observation.ttft_ms == 10
    observation.accept("error", {"stop_reason": "timeout"}, 21)
    with pytest.raises(ValueError, match="timeout"):
        observation.validate()
    with pytest.raises(ValueError, match="missing_terminal"):
        StreamObservation().validate()


@pytest.mark.parametrize("data", [b"data: []\n\n", b"data: not-json\n\n", b"data: {}\n"])
def test_invalid_or_truncated_sse(data):
    parser = SSEParser()
    with pytest.raises(ValueError):
        parser.feed(data, final=True)


def test_final_only_paths_and_failed_terminal():
    observation = StreamObservation()
    observation.accept("final", {"status": "success", "final_answer": "memory"}, 2)
    observation.validate()
    assert observation.ttft_ms is None
    with pytest.raises(ValueError, match="missing_answer_token"):
        observation.validate(expect_token=True)
    observation = StreamObservation()
    observation.accept("final", {"status": "failed", "final_answer": "fallback"}, 2)
    with pytest.raises(ValueError, match="terminal_status"):
        observation.validate()


def summary(total=5000, first=3000):
    return summarize(
        [
            {"success": True, "scenario_ms": total, "ttft_ms": first},
            {"success": True, "scenario_ms": total, "ttft_ms": None},
        ]
    )


def test_percentiles_missing_samples_and_nonfinite_values():
    assert percentile([], 0.5) is None
    assert percentile([30, 10, 20, 40], 0.5) == 25
    assert percentile([30, 10, 20, 40], 0.95) == pytest.approx(38.5)
    assert summary()["ttft_ms"]["n"] == 1
    for value in [-1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            percentile([value], 0.5)


def test_baseline_median_and_exact_thresholds():
    identity = {"host": "test"}
    baseline = make_baseline([summary(5000), summary(6000), summary(4000)], identity)
    assert baseline["metrics"]["scenario_ms"]["p50"] == 5000
    # Equality at either threshold does not fail.
    assert not compare_baseline(summary(6000), baseline, identity)["failed"]
    assert compare_baseline(summary(6001), baseline, identity)["failed"]
    assert not compare_baseline(summary(5000, 4000), baseline, identity)["failed"]
    assert compare_baseline(summary(5000, 4001), baseline, identity)["failed"]
    # A relative increase alone is insufficient.
    small = make_baseline([summary(100)] * 3, identity)
    assert not compare_baseline(summary(900), small, identity)["failed"]
    # An absolute increase alone is insufficient.
    large = make_baseline([summary(10000)] * 3, identity)
    assert not compare_baseline(summary(11001), large, identity)["failed"]


def test_baseline_fingerprint_sample_count_and_failed_rounds():
    baseline = make_baseline([summary()] * 3, {"profile": "real"})
    with pytest.raises(ValueError, match="fingerprint"):
        compare_baseline(summary(), baseline, {"profile": "offline"})
    altered = summary()
    altered["ttft_ms"]["n"] = 2
    with pytest.raises(ValueError, match="sample"):
        compare_baseline(altered, baseline, {"profile": "real"})
    failed = summary()
    failed["failure_count"] = 1
    with pytest.raises(ValueError, match="successful"):
        make_baseline([summary(), failed, summary()], {})
    with pytest.raises(ValueError, match="three"):
        make_baseline([summary()], {})


def test_tail_regression_only_warns():
    baseline = make_baseline([summary()] * 3, {})
    current = summary()
    current["scenario_ms"]["p95"] = 15000
    result = compare_baseline(current, json.loads(json.dumps(baseline)), {})
    assert not result["failed"]
    assert any(row["regressed"] and not row["hard_gate"] for row in result["comparisons"])

"""Pure parsing/statistics for the local performance runner (no API calls)."""

from __future__ import annotations

import codecs
import json
import math
import re
import statistics
from collections import Counter
from typing import Any

SCHEMA_VERSION = 1
METRICS = ("scenario_ms", "ttft_ms")


class SSEParser:
    """Incremental UTF-8 SSE decoder, including CRLF and multi-line data."""

    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self.buffer = ""
        self.event = ""
        self.data: list[str] = []
        self.frame_size = 0

    def feed(self, chunk: bytes, *, final: bool = False) -> list[tuple[str, dict[str, Any]]]:
        self.buffer += self.decoder.decode(chunk, final=final)
        events = []
        while match := re.search(r"\r\n|\r|\n", self.buffer):
            if match.group() == "\r" and match.end() == len(self.buffer) and not final:
                break
            line = self.buffer[: match.start()]
            self.buffer = self.buffer[match.end() :]
            self.frame_size += len(line)
            if self.frame_size > 2_000_000:
                raise ValueError("SSE frame exceeds 2 MB")
            if not line:
                if self.data:
                    payload = json.loads("\n".join(self.data))
                    if not isinstance(payload, dict):
                        raise ValueError("SSE data must be a JSON object")
                    events.append((self.event or str(payload.get("type", "message")), payload))
                self.event, self.data, self.frame_size = "", [], 0
            elif not line.startswith(":"):
                field, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if field == "event":
                    self.event = value
                elif field == "data":
                    self.data.append(value)
        if len(self.buffer) > 2_000_000:
            raise ValueError("SSE line exceeds 2 MB")
        if final and (self.buffer or self.data):
            raise ValueError("Incomplete SSE frame at EOF")
        return events


class StreamObservation:
    def __init__(self) -> None:
        self.ttft_ms: float | None = None
        self.terminal: dict[str, Any] | None = None
        self.error: str | None = None
        self.events: Counter = Counter()

    def accept(self, event: str, payload: dict[str, Any], elapsed_ms: float) -> None:
        self.events[event] += 1
        if self.terminal is not None:
            self.error = "event_after_terminal"
        if event == "token" and str(payload.get("delta") or "").strip() and self.ttft_ms is None:
            self.ttft_ms = elapsed_ms
        if event == "error":
            self.error = str(payload.get("stop_reason") or payload.get("message") or "SSE error")
        if event == "final":
            self.terminal = payload
            if payload.get("status") != "success":
                self.error = "terminal_status:" + str(payload.get("status"))

    def validate(self, *, expect_token: bool = False) -> None:
        if self.error:
            raise ValueError(self.error)
        if self.terminal is None:
            raise ValueError("missing_terminal_event")
        if not str(self.terminal.get("final_answer") or "").strip():
            raise ValueError("empty_final_answer")
        if expect_token and self.ttft_ms is None:
            raise ValueError("missing_answer_token")


def percentile(values: list[float], probability: float) -> float | None:
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between 0 and 1")
    if not values:
        return None
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("durations must be finite and nonnegative")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [case for case in cases if case["success"]]
    return {
        "case_count": len(cases),
        "success_count": len(successful),
        "success_rate": len(successful) / len(cases) if cases else 0,
        "failure_count": len(cases) - len(successful),
        "timeout_count": sum("timeout" in str(case.get("error", "")).lower() for case in cases),
        **{
            metric: distribution([float(case[metric]) for case in successful if case.get(metric) is not None])
            for metric in METRICS
        },
    }


def make_baseline(rounds: list[dict[str, Any]], fingerprint: dict[str, Any]) -> dict[str, Any]:
    if len(rounds) != 3:
        raise ValueError("baseline requires exactly three rounds")
    if any(row["failure_count"] or not row["case_count"] for row in rounds):
        raise ValueError("baseline requires three successful rounds")
    if len({row["case_count"] for row in rounds}) != 1:
        raise ValueError("baseline case counts differ")
    for metric in METRICS:
        counts = {row[metric]["n"] for row in rounds}
        if len(counts) != 1 or not all(row[metric]["p50"] is not None for row in rounds):
            raise ValueError("baseline eligible sample counts differ or are empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "case_count": rounds[0]["case_count"],
        "metrics": {
            metric: {
                "n": rounds[0][metric]["n"],
                **{key: statistics.median(row[metric][key] for row in rounds) for key in ("p50", "p95", "p99")},
            }
            for metric in METRICS
        },
        "rounds": rounds,
    }


def validate_baseline(baseline: dict, fingerprint: dict, *, case_count: int | None = None) -> None:
    if baseline.get("schema_version") != SCHEMA_VERSION or baseline.get("fingerprint") != fingerprint:
        raise ValueError("baseline environment/configuration fingerprint mismatch")
    if not isinstance(baseline.get("case_count"), int) or baseline["case_count"] <= 0:
        raise ValueError("baseline case count is invalid")
    if case_count is not None and baseline["case_count"] != case_count:
        raise ValueError("baseline case count mismatch")
    for metric in METRICS:
        reference = baseline.get("metrics", {}).get(metric, {})
        count = reference.get("n")
        if not isinstance(count, int) or not 0 < count <= baseline["case_count"]:
            raise ValueError("baseline sample count is invalid: " + metric)
        for key in ("p50", "p95", "p99"):
            value = reference.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("baseline percentile is invalid: " + metric)
        if not reference["p50"] <= reference["p95"] <= reference["p99"]:
            raise ValueError("baseline percentile ordering is invalid: " + metric)


def compare_baseline(summary: dict, baseline: dict, fingerprint: dict) -> dict:
    validate_baseline(baseline, fingerprint, case_count=summary["case_count"])
    comparisons = []
    for metric in METRICS:
        reference = baseline["metrics"][metric]
        current = summary[metric]
        if not summary["failure_count"] and reference["n"] != current["n"]:
            raise ValueError("baseline eligible sample count mismatch: " + metric)
        for key in ("p50", "p95", "p99"):
            before, after = reference[key], current[key]
            if before is None or after is None or before < 0 or not math.isfinite(before):
                raise ValueError("baseline or measured percentile is invalid: " + metric)
            increase = after - before
            relative = increase / before if before else (math.inf if increase > 0 else 0)
            limit, absolute = (0.20, 1000) if key == "p50" else (0.30, 2000)
            regressed = increase > absolute and after > before * (1 + limit)
            comparisons.append(
                {
                    "metric": metric,
                    "percentile": key,
                    "baseline_ms": before,
                    "current_ms": after,
                    "increase_ms": increase,
                    "increase_ratio": relative if math.isfinite(relative) else None,
                    "regressed": regressed,
                    "hard_gate": key == "p50",
                }
            )
    return {
        "failed": bool(summary["failure_count"]) or any(row["regressed"] and row["hard_gate"] for row in comparisons),
        "comparisons": comparisons,
    }

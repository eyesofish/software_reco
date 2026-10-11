"""Managed local HTTP/SSE evaluation and explicit performance baselines."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .performance_metrics import (
    SCHEMA_VERSION,
    SSEParser,
    StreamObservation,
    compare_baseline,
    distribution,
    make_baseline,
    summarize,
    validate_baseline,
)
from .process_support import stop_process

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("performance_scenarios.json")
REPORTS = ROOT / "evaluation" / "reports" / "performance"
PROFILE = {
    "RECALL_ENABLE_WEB": False,
    "RECALL_ENABLE_KEYWORD": False,
    "RECALL_ENABLE_IMAGE_VECTOR": False,
    "RECALL_ENABLE_VECTOR": True,
    "RECALL_ENABLE_MEMORY": True,
    "ENABLE_PARENT_CHILD_CHUNKING": False,
    "EVIDENCE_EVAL_USE_LLM": False,
    "LANGSMITH_TRACING": False,
    "RATE_LIMIT_RECOMMEND": "10000/minute",
}
OFFLINE_PROFILE = {
    "ROUTER_ENABLE": False,
    "QUERY_NORMALIZATION_USE_LLM": False,
    "SKILL_ROUTER_USE_LLM": False,
    "RERANK_MODEL_ENABLED": False,
    "AGENT_LOOP_ENABLE": False,
    "OPENAI_API_KEY": "offline",
    "DASHSCOPE_API_KEY": "offline",
    "API_KEY": "",
    "EMBEDDING_PROVIDER": "dashscope",
    "EMBEDDING_ENABLE_FALLBACK": False,
    "LLM_MAX_RETRIES": 0,
}


def load_manifest(path: Path = MANIFEST) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    counts = {group: sum(case["group"] == group for case in cases) for group in ("direct", "rag", "hitl", "memory")}
    if counts != {"direct": 10, "rag": 10, "hitl": 5, "memory": 5}:
        raise ValueError("manifest must contain 10 direct, 10 RAG, 5 HITL and 5 memory cases")
    if len({case["id"] for case in cases}) != len(cases) or not manifest["corpus"]:
        raise ValueError("manifest needs unique case IDs and a nonempty corpus")
    return manifest


def profile_settings(offline: bool = False):
    from app.core.config import Settings

    return Settings().model_copy(update={**PROFILE, **(OFFLINE_PROFILE if offline else {})})


def fingerprint(settings, manifest: Path = MANIFEST, *, offline: bool = False) -> dict:
    configuration = {}
    for key, value in settings.model_dump().items():
        if any(part in key for part in ("API_KEY", "PASSWORD", "SECRET")) or key in {
            "CHROMA_DB_PATH",
            "INGEST_PATH",
            "SESSION_STATE_FILE",
            "MEMORY_STORE_FILE",
            "SERVER_HOST",
            "SERVER_PORT",
        }:
            continue
        if "URL" in key and isinstance(value, str) and value:
            parsed = urlsplit(value)
            # Credentials and query parameters are never baseline metadata.
            hostname = parsed.hostname or ""
            if ":" in hostname:
                hostname = "[" + hostname + "]"
            authority = hostname + (":" + str(parsed.port) if parsed.port else "")
            value = urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
        configuration[key] = value
    dependencies = sorted(
        {
            (package.metadata["Name"].lower(), package.version)
            for package in importlib.metadata.distributions()
            if package.metadata.get("Name")
        }
    )
    return {
        "profile": "offline" if offline else "real",
        "protocol_version": SCHEMA_VERSION,
        "host": socket.gethostname(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "dependencies": dependencies,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "configuration": configuration,
        "request_settings": {"timeout": 60, "max_iterations": 3, "concurrency": 1, "warmups_per_group": 1},
    }


def canonical_fingerprint(value: dict) -> dict:
    # Normalize tuples to JSON arrays before comparing with deserialized baselines.
    return json.loads(json.dumps(value, sort_keys=True))


def read_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # An active worker may still be appending the last line.
            continue
    return records


def environment_for_server(runtime: Path, *, offline: bool) -> dict[str, str]:
    settings = profile_settings(offline)
    environment = os.environ.copy()
    for key, value in settings.model_dump().items():
        environment[key] = str(value).lower() if isinstance(value, bool) else str(value)
    ingest = runtime / "empty-ingest"
    ingest.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "PERFORMANCE_RUNTIME_DIR": str(runtime),
            "PERFORMANCE_TRACE_FILE": str(runtime / "spans.jsonl"),
            "PERFORMANCE_OFFLINE": "1" if offline else "0",
            "PERFORMANCE_MANIFEST": str(MANIFEST),
            "CHROMA_DB_PATH": str(runtime / "chroma"),
            "INGEST_PATH": str(ingest),
            "SESSION_STATE_FILE": str(runtime / "sessions.json"),
            "MEMORY_STORE_FILE": str(runtime / "memory.json"),
            "LANGGRAPH_CHECKPOINT_PATH": str(runtime / "checkpoints.sqlite"),
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "LANGSMITH_API_KEY": "",
            "ANONYMIZED_TELEMETRY": "False",
            "HF_HUB_OFFLINE": "1" if offline else environment.get("HF_HUB_OFFLINE", "0"),
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


@contextmanager
def managed_server(runtime: Path, *, offline: bool = False, startup_timeout: float = 180):
    """Start only our own server; terminate it on success, error or interrupt."""
    runtime.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    started = time.perf_counter()
    process = None
    with (runtime / "server.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "evaluation.performance_app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=environment_for_server(runtime, offline=offline),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            with httpx.Client(timeout=1, trust_env=False) as client:
                ready = False
                while time.perf_counter() - started < startup_timeout:
                    if process.poll() is not None:
                        raise RuntimeError(f"Evaluation server exited during setup; see {runtime / 'server.log'}")
                    try:
                        if client.get(base_url + "/health").status_code == 200:
                            setup = json.loads((runtime / "setup.json").read_text(encoding="utf-8"))
                            setup["startup_ms"] = (time.perf_counter() - started) * 1000
                            ready = True
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.10)
            if not ready:
                raise RuntimeError(f"Evaluation server startup timeout; see {runtime / 'server.log'}")
            yield base_url, setup
        finally:
            if process is not None:
                stop_process(process)


def request_json(client: httpx.Client, base_url: str, path: str, body: dict, request_id: str) -> tuple[dict, dict]:
    started = time.perf_counter()
    response = client.post(base_url + path, json=body, headers={"X-Request-Id": request_id})
    response.raise_for_status()
    if response.headers.get("X-Request-Id") != request_id:
        raise ValueError("request ID was not propagated")
    payload = response.json()
    return payload, {
        "request_id": request_id,
        "path": path,
        "http_status": response.status_code,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
    }


def request_stream(client: httpx.Client, base_url: str, path: str, body: dict, request_id: str):
    started = time.perf_counter()
    parser = SSEParser()
    observation = StreamObservation()
    with client.stream("POST", base_url + path, json=body, headers={"X-Request-Id": request_id}) as response:
        response.raise_for_status()
        if response.headers.get("X-Request-Id") != request_id:
            raise ValueError("request ID was not propagated")
        if "text/event-stream" not in response.headers.get("content-type", ""):
            raise ValueError("response is not SSE")
        for chunk in response.iter_bytes():
            elapsed = (time.perf_counter() - started) * 1000
            for event, payload in parser.feed(chunk):
                observation.accept(event, payload, elapsed)
        for event, payload in parser.feed(b"", final=True):
            observation.accept(event, payload, (time.perf_counter() - started) * 1000)
    measurement = {
        "request_id": request_id,
        "path": path,
        "http_status": response.status_code,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "ttft_ms": observation.ttft_ms,
        "events": dict(observation.events),
    }
    return observation, measurement


def execute_case(client: httpx.Client, base_url: str, case: dict, runtime: Path, *, phase: str = "measured") -> dict:
    identifier = f"{phase}-{case['id']}-{uuid.uuid4().hex[:8]}"
    requests, preparations = [], []
    result = {
        "id": case["id"],
        "group": case["group"],
        "phase": phase,
        "session_id": identifier,
        "success": False,
        "scenario_ms": None,
        "ttft_ms": None,
        "requests": requests,
        "preparations": preparations,
    }
    body = {
        "query": case["query"],
        "session_id": identifier,
        "timeout": 60,
        "max_iterations": 3,
        "hitl_policy": "human" if case["group"] == "hitl" else "auto_confirm",
    }
    started = time.perf_counter()
    try:
        if "prepare_query" in case:
            payload, measurement = request_json(
                client,
                base_url,
                "/api/v1/recommend",
                {**body, "query": case["prepare_query"]},
                identifier + "-prep",
            )
            preparations.append(measurement)
            if payload.get("status") != "success" or not payload.get("final_answer"):
                raise ValueError("memory preparation failed")
        if case["group"] == "hitl":
            payload, measurement = request_json(client, base_url, "/api/v1/recommend", body, identifier + "-initial")
            requests.append(measurement)
            if payload.get("status") != "awaiting_human_confirmation" or not payload.get("pending_sub_questions"):
                raise ValueError("expected HITL interruption")
            if payload.get("session_id") != identifier:
                raise ValueError("HITL session mismatch")
            observation, measurement = request_stream(
                client,
                base_url,
                "/api/v1/recommend/confirm/stream",
                {"session_id": identifier, "action": "confirm"},
                identifier + "-confirm",
            )
        else:
            observation, measurement = request_stream(client, base_url, "/api/v1/recommend/stream", body, identifier)
        requests.append(measurement)
        observation.validate(expect_token=case.get("expect_token", False))
        terminal = observation.terminal
        if terminal.get("session_id") != identifier:
            raise ValueError("terminal session mismatch")
        if case.get("expect_docs") and not terminal.get("retrieved_doc_ids"):
            raise ValueError("expected retrieved evidence")
        if case.get("expected_name") and case["expected_name"] not in terminal["final_answer"]:
            raise ValueError("memory answer did not retain the prepared name")
        if case["group"] == "hitl" and (terminal.get("hitl") or {}).get("decision") != "confirm":
            raise ValueError("HITL confirmation was not preserved")
        request_ids = {row["request_id"] for row in requests}
        deadline = time.monotonic() + 2
        while True:
            spans = [row for row in read_trace(runtime / "spans.jsonl") if row.get("request_id") in request_ids]
            completed = {row["request_id"] for row in spans if row.get("stage") == "http" and row.get("complete")}
            if completed == request_ids:
                break
            if time.monotonic() >= deadline:
                raise ValueError("missing completed HTTP timing span")
            time.sleep(0.01)
        if case.get("expected_mode"):
            modes = [row.get("mode") for row in spans if row.get("stage") == "node.routing_node"]
            if modes != [case["expected_mode"]]:
                raise ValueError("unexpected routing branch: " + str(modes))
        if any(row.get("fallback_used") or row.get("outcome") == "error" for row in spans):
            raise ValueError("dependency failure or routing fallback in timing trace")
        result["success"] = True
        result["answer_length"] = len(terminal["final_answer"])
        result["retrieved_doc_ids"] = terminal.get("retrieved_doc_ids", [])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["scenario_ms"] = (
        sum(row["elapsed_ms"] for row in requests)
        if requests
        else ((time.perf_counter() - started) * 1000 - sum(row["elapsed_ms"] for row in preparations))
    )
    if requests and requests[-1].get("ttft_ms") is not None:
        result["ttft_ms"] = sum(row["elapsed_ms"] for row in requests[:-1]) + requests[-1]["ttft_ms"]
    return result


def execute_round(output: Path, settings, manifest: dict, *, offline: bool = False) -> dict:
    measured, warmups = [], []
    with (
        managed_server(output, offline=offline) as (base_url, setup),
        httpx.Client(
            timeout=httpx.Timeout(90, connect=5),
            headers={"X-API-Key": settings.API_KEY},
            trust_env=False,
        ) as client,
    ):
        for group in ("direct", "rag", "hitl", "memory"):
            case = next(case for case in manifest["cases"] if case["group"] == group)
            print(f"Warmup: {group}", flush=True)
            result = execute_case(client, base_url, case, output, phase="warmup")
            warmups.append(result)
            if not result["success"]:
                return {
                    "setup": setup,
                    "warmups": warmups,
                    "cases": measured,
                    "summary": summarize(measured),
                    "trace": read_trace(output / "spans.jsonl"),
                    "setup_error": f"Warmup failed: {group}: {result.get('error')}",
                }
        for index, case in enumerate(manifest["cases"], 1):
            result = execute_case(client, base_url, case, output)
            measured.append(result)
            print(
                f"{index}/30 {case['id']}: {'PASS' if result['success'] else 'FAIL'} {result['scenario_ms']:.1f} ms",
                flush=True,
            )
    return {
        "setup": setup,
        "warmups": warmups,
        "cases": measured,
        "summary": summarize(measured),
        "trace": read_trace(output / "spans.jsonl"),
    }


def write_reports(output: Path, report: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cases = [case for row in report.get("rounds", []) for case in row["cases"]]
    with (output / "cases.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "round",
                "id",
                "group",
                "success",
                "scenario_ms",
                "ttft_ms",
                "error",
            ],
        )
        writer.writeheader()
        for number, row in enumerate(report.get("rounds", []), 1):
            for case in row["cases"]:
                writer.writerow(
                    {"round": number, **{key: case.get(key) for key in writer.fieldnames if key != "round"}}
                )
    lines = [
        "# Local performance evaluation",
        "",
        f"- Mode: {report['mode']}",
        f"- Result: {report['status']}",
        f"- Commit: {report.get('commit', 'unknown')}",
        "- Units: milliseconds; cold setup, warmups and memory preparation are excluded.",
        "- TTFT counts only nonempty answer tokens; final-only memory responses have no TTFT.",
        "- P95/P99 from 30 fixed scenarios are descriptive, not hard gates.",
        "- Scenario time sums formal HTTP turns; HITL confirmation delay is excluded.",
        "",
    ]
    if report.get("error"):
        lines.extend(["## Error", "", str(report["error"]), ""])
    for number, row in enumerate(report.get("rounds", []), 1):
        summary = row["summary"]
        lines.extend(
            [
                f"## Round {number}",
                "",
                f"Success: {summary['success_count']}/{summary['case_count']}; "
                f"timeouts: {summary['timeout_count']}; startup: {row['setup']['startup_ms']:.1f} ms",
                "",
                "| Metric | Samples | P50 | P95 | P99 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name in ("scenario_ms", "ttft_ms"):
            stats = summary[name]
            lines.append(f"| {name} | {stats['n']} | {stats['p50']} | {stats['p95']} | {stats['p99']} |")
        for group in ("direct", "rag", "hitl", "memory"):
            group_summary = summarize([case for case in row["cases"] if case["group"] == group])
            lines.append(
                f"\n{group}: {group_summary['success_count']}/{group_summary['case_count']}; "
                f"P50 {group_summary['scenario_ms']['p50']} ms"
            )
        lines.extend(
            [
                "",
                "### Internal observations",
                "",
                "Nested and parallel observations overlap. Do not add them to obtain total time.",
                "",
                "| Stage | Count | P50 | P95 |",
                "|---|---:|---:|---:|",
            ]
        )
        request_ids = {request["request_id"] for case in row["cases"] for request in case["requests"]}
        stages = defaultdict(list)
        for record in row["trace"]:
            if record.get("request_id") in request_ids and "elapsed_ms" in record:
                label = record["stage"] + (":" + record["scene"] if record.get("scene") else "")
                stages[(record["kind"], label)].append(record["elapsed_ms"])
        for (kind, name), values in sorted(stages.items()):
            stats = distribution(values)
            lines.append(f"| {kind}/{name} | {stats['n']} | {stats['p50']:.1f} | {stats['p95']:.1f} |")
    if report.get("comparison"):
        lines.extend(
            [
                "",
                "## Baseline comparison",
                "",
                "Hard gate: P50 exceeds baseline by both >20% and >1000 ms.",
                "",
                "| Metric | Percentile | Baseline | Current | Increase | Result |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for row in report["comparison"]["comparisons"]:
            state = ("FAIL" if row["hard_gate"] else "WARN") if row["regressed"] else "PASS"
            lines.append(
                f"| {row['metric']} | {row['percentile']} | {row['baseline_ms']:.1f} | "
                f"{row['current_ms']:.1f} | {row['increase_ms']:.1f} | {state} |"
            )
    lines.extend(["", "## Slowest scenarios", ""])
    for case in sorted(cases, key=lambda case: case["scenario_ms"], reverse=True)[:10]:
        error = f"; {case['error']}" if case.get("error") else ""
        lines.append(f"- {case['id']}: {case['scenario_ms']:.1f} ms" + error)
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "real", "baseline"], required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = (args.output or REPORTS / identifier).resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create a new report directory: {exc}", file=sys.stderr)
        return 2
    report = {"schema_version": SCHEMA_VERSION, "mode": args.mode, "status": "environment_error", "rounds": []}
    code = 2
    try:
        if sys.version_info < (3, 11):  # noqa: UP036 - explicit CLI environment diagnostic
            raise ValueError("Python 3.11 or newer is required")
        settings = profile_settings(args.mode == "offline")
        manifest = load_manifest()
        identity = canonical_fingerprint(fingerprint(settings, offline=args.mode == "offline"))
        report["fingerprint"] = identity
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        report["commit"] = git.stdout.strip() if git.returncode == 0 else "unknown"
        baseline = None
        if args.mode == "real":
            if args.baseline is None:
                raise ValueError("real mode requires --baseline; create one with --mode baseline")
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            validate_baseline(baseline, identity, case_count=len(manifest["cases"]))
            expected_tokens = sum(bool(case.get("expect_token")) for case in manifest["cases"])
            if baseline["metrics"]["ttft_ms"]["n"] != expected_tokens:
                raise ValueError("baseline eligible token sample count mismatch")
        if args.mode != "offline" and not (settings.OPENAI_API_KEY or settings.DASHSCOPE_API_KEY):
            raise ValueError("real model credentials are not configured")
        for number in range(3 if args.mode == "baseline" else 1):
            print(f"Starting {args.mode} round {number + 1}; output: {output}", flush=True)
            row = execute_round(output / f"round-{number + 1}", settings, manifest, offline=args.mode == "offline")
            report["rounds"].append(row)
            if row.get("setup_error"):
                report["error"] = row["setup_error"]
                break
            if row["summary"]["failure_count"]:
                report["status"], code = "failed", 1
                break
        else:
            report["status"], code = "passed", 0
            if args.mode == "baseline":
                reference = make_baseline([row["summary"] for row in report["rounds"]], identity)
                reference["commit"] = report["commit"]
                reference["created_at"] = datetime.now(UTC).isoformat()
                (output / "baseline.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")
            elif baseline:
                report["comparison"] = compare_baseline(report["rounds"][0]["summary"], baseline, identity)
                if report["comparison"]["failed"]:
                    report["status"], code = "regression", 1
    except (OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr, flush=True)
    finally:
        report["exit_code"] = code
        write_reports(output, report)
        print(f"REPORT={output / 'report.md'}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

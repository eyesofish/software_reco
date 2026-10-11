"""Run the same offline functional checks locally and in CI."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from .process_support import stop_process

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = (
        args.output
        or ROOT
        / "evaluation"
        / "reports"
        / "checks"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    ).resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create a new report directory: {exc}", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    runtime = output / "test-runtime"
    runtime.mkdir()
    ingest = runtime / "empty-ingest"
    ingest.mkdir()
    environment.update(
        {
            "SOFTWARE_RECO_OFFLINE_TESTS": "1",
            "LOCAL_CHECK_REPORT_DIR": str(output),
            "PERFORMANCE_RUNTIME_DIR": str(runtime),
            "CHROMA_DB_PATH": str(runtime / "chroma"),
            "MEMORY_STORE_FILE": str(runtime / "memory.json"),
            "SESSION_STATE_FILE": str(runtime / "sessions.json"),
            "LANGGRAPH_CHECKPOINT_PATH": str(runtime / "checkpoints.sqlite"),
            "INGEST_PATH": str(ingest),
            "OPENAI_API_KEY": "offline",
            "DASHSCOPE_API_KEY": "offline",
            "TAVILY_API_KEY": "",
            "API_KEY": "",
            "ADMIN_API_KEY": "",
            "LANGSMITH_API_KEY": "",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "HF_HUB_OFFLINE": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "RUN_ES_INTEGRATION": "0",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    environment.pop("PERFORMANCE_TRACE_FILE", None)
    steps = []
    started = time.perf_counter()
    commands = [
        ("lint", ["-m", "ruff", "check", "."], True),
        ("types", ["-m", "mypy", "--strict", "--ignore-missing-imports", "software_recommend_system", "app"], False),
        (
            "tests",
            [
                "-m",
                "pytest",
                "tests/",
                "-v",
                "--durations=20",
                "--durations-min=0",
                "--junitxml=" + str(output / "junit.xml"),
                "--basetemp=" + str(output / "pytest-temp"),
            ],
            True,
        ),
    ]
    code = 0
    required = ("ruff", "mypy", "pytest", "pytest_asyncio")
    missing = [module for module in required if importlib.util.find_spec(module) is None]
    if sys.version_info[:2] < (3, 11) or missing:  # noqa: UP036 - explicit CLI environment diagnostic
        code = 2
        steps.append(
            {
                "name": "environment",
                "exit_code": 2,
                "hard_gate": True,
                "elapsed_ms": 0,
                "error": "Python >=3.11 and dev dependencies required; missing: " + ", ".join(missing),
            }
        )
    else:
        for name, arguments, hard_gate in commands:
            print(f"Running {name}; log: {output / (name + '.log')}", flush=True)
            step_started = time.perf_counter()
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, *arguments],
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                try:
                    step_code = process.wait(timeout=120 if name == "types" else None)
                except subprocess.TimeoutExpired:
                    step_code = 124
                    log.write("\nAdvisory type check exceeded 120 seconds; continuing to tests.\n")
                finally:
                    stop_process(process)
            steps.append(
                {
                    "name": name,
                    "exit_code": step_code,
                    "hard_gate": hard_gate,
                    "elapsed_ms": (time.perf_counter() - step_started) * 1000,
                }
            )
            if step_code and hard_gate:
                code = 1
            print(f"{name}: {'PASS' if not step_code else 'FAIL' if hard_gate else 'ADVISORY'}", flush=True)
    tests = []
    totals = {}
    if (output / "junit.xml").exists():
        tree = ET.parse(output / "junit.xml")
        totals = {
            key: sum(int(suite.get(key, "0")) for suite in tree.iter("testsuite"))
            for key in ("tests", "failures", "errors", "skipped")
        }
        tests = sorted(
            [
                {
                    "name": case.get("classname", "") + "." + case.get("name", ""),
                    "elapsed_ms": float(case.get("time", "0")) * 1000,
                    "skipped": case.find("skipped") is not None,
                }
                for case in tree.iter("testcase")
            ],
            key=lambda row: row["elapsed_ms"],
            reverse=True,
        )
    report = {
        "mode": "offline",
        "exit_code": code,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "steps": steps,
        "totals": totals,
        "slowest_tests": tests[:20],
    }
    (output / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Offline local checks",
        "",
        f"Exit code: {code}; total: {report['elapsed_ms']:.1f} ms",
        "",
        "External DNS and connections are prohibited; loopback HTTP is permitted.",
        "Live Elasticsearch integration tests are explicitly skipped. Type checking is advisory.",
        "",
        "| Step | Exit | Gate | Milliseconds |",
        "|---|---:|---|---:|",
    ]
    for step in steps:
        lines.append(
            f"| {step['name']} | {step['exit_code']} | {'hard' if step['hard_gate'] else 'advisory'} | "
            f"{step['elapsed_ms']:.1f} |"
        )
        if step.get("error"):
            lines.append("\n" + step["error"])
    lines.extend(["", f"Tests: {totals}", "", "## Slowest 20 tests", ""])
    lines.extend(f"- {row['name']}: {row['elapsed_ms']:.1f} ms" for row in tests[:20])
    lines.extend(
        [
            "",
            "HTTP/SSE scenario timings and stage observations: pytest-temp/**/http/report.md",
            "",
            "These offline durations are separate from real model performance baselines.",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"REPORT={output / 'report.md'}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

import json
from types import SimpleNamespace

import pytest

from evaluation import performance_runner as runner
from evaluation.performance_metrics import summarize


@pytest.fixture
def fake_rounds(monkeypatch):
    # This checks CLI control flow only; these numbers are never real baselines.
    identity = {"profile": "real", "test": True}
    settings = SimpleNamespace(OPENAI_API_KEY="test-secret", DASHSCOPE_API_KEY="", API_KEY="")
    monkeypatch.setattr(runner, "profile_settings", lambda *args: settings)
    monkeypatch.setattr(runner, "fingerprint", lambda *args, **kwargs: identity)
    calls = []
    durations = [4000, 5000, 6000]

    def execute(output, settings, manifest, **kwargs):
        calls.append(output)
        duration = durations[min(len(calls) - 1, len(durations) - 1)]
        cases = [
            {
                "id": case["id"],
                "group": case["group"],
                "success": True,
                "scenario_ms": duration,
                "ttft_ms": 3000 if case["expect_token"] else None,
                "requests": [],
            }
            for case in manifest["cases"]
        ]
        return {"setup": {"startup_ms": 0}, "cases": cases, "summary": summarize(cases), "trace": [], "warmups": []}

    monkeypatch.setattr(runner, "execute_round", execute)
    return identity, calls, durations


def test_cli_three_round_baseline_and_hard_regression(tmp_path, fake_rounds):
    _, calls, durations = fake_rounds
    output = tmp_path / "baseline-run"
    assert runner.main(["--mode", "baseline", "--output", str(output)]) == 0
    baseline = output / "baseline.json"
    saved = json.loads(baseline.read_text())
    assert len(calls) == 3
    assert saved["metrics"]["scenario_ms"]["p50"] == 5000
    durations[:] = [6001]
    assert runner.main(["--mode", "real", "--baseline", str(baseline), "--output", str(tmp_path / "comparison")]) == 1
    report = json.loads((tmp_path / "comparison" / "results.json").read_text())
    assert report["status"] == "regression"
    assert not (tmp_path / "comparison" / "baseline.json").exists()


def test_missing_mismatched_and_malformed_baseline_prevent_calls(tmp_path, fake_rounds):
    _, calls, _ = fake_rounds
    assert runner.main(["--mode", "real", "--output", str(tmp_path / "missing")]) == 2
    assert calls == []
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"schema_version": 1, "fingerprint": {"other": True}}))
    assert runner.main(["--mode", "real", "--baseline", str(invalid), "--output", str(tmp_path / "mismatch")]) == 2
    assert calls == []
    assert (tmp_path / "mismatch" / "report.md").exists()


def test_failed_round_never_produces_baseline(tmp_path, fake_rounds, monkeypatch):
    original = runner.execute_round

    def fail(*args, **kwargs):
        row = original(*args, **kwargs)
        row["cases"][0]["success"] = False
        row["cases"][0]["error"] = "injected"
        row["summary"] = summarize(row["cases"])
        return row

    monkeypatch.setattr(runner, "execute_round", fail)
    output = tmp_path / "failed"
    assert runner.main(["--mode", "baseline", "--output", str(output)]) == 1
    assert not (output / "baseline.json").exists()
    assert (output / "cases.csv").exists()


def test_warmup_failure_retains_raw_round_and_environment_exit(tmp_path, fake_rounds, monkeypatch):
    monkeypatch.setattr(
        runner,
        "execute_round",
        lambda *args, **kwargs: {
            "setup": {"startup_ms": 1},
            "cases": [],
            "warmups": [{"success": False, "error": "injected"}],
            "summary": summarize([]),
            "trace": [],
            "setup_error": "injected warmup failure",
        },
    )
    output = tmp_path / "warmup"
    assert runner.main(["--mode", "baseline", "--output", str(output)]) == 2
    report = json.loads((output / "results.json").read_text())
    assert report["rounds"][0]["warmups"][0]["success"] is False
    assert not (output / "baseline.json").exists()


def test_fingerprint_excludes_credentials_and_runtime_paths():
    settings = SimpleNamespace(
        model_dump=lambda: {
            "OPENAI_API_KEY": "must-not-leak",
            "API_KEY": "must-not-leak",
            "MEMORY_STORE_FILE": "private-path",
            "OPENAI_BASE_URL": "https://user:password@example.com/v1?token=must-not-leak",
            "LLM_MODEL": "fixed-model",
        }
    )
    identity = runner.fingerprint(settings)
    text = json.dumps(identity)
    assert "must-not-leak" not in text
    assert "private-path" not in text
    assert "password" not in text
    assert identity["configuration"]["OPENAI_BASE_URL"] == "https://example.com/v1"

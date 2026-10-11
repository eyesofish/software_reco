import asyncio
import json
import logging

import pytest

from software_recommend_system.execution_control import run_controlled, stream_controlled
from software_recommend_system.logging_utils import log_event
from software_recommend_system.performance import TimingMiddleware, span


@pytest.mark.asyncio
async def test_stream_lifetime_and_parallel_worker_correlation(tmp_path, monkeypatch):
    destination = tmp_path / "spans.jsonl"
    monkeypatch.setenv("PERFORMANCE_TRACE_FILE", str(destination))
    waiting = asyncio.Event()
    count = 0

    def operation(control):
        with span("worker"):
            return control.run_id

    async def app(scope, receive, send):
        nonlocal count
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await run_controlled(operation, session_id=scope["path"], timeout_seconds=2)
        async for _item, _control in stream_controlled(
            lambda control: iter([operation(control)]), session_id=scope["path"], timeout_seconds=2
        ):
            pass
        await send({"type": "http.response.body", "body": b"meta", "more_body": True})
        count += 1
        if count == 2:
            waiting.set()
        await waiting.wait()
        # Headers alone must not close the HTTP timing span.
        assert all(row["stage"] != "http" for row in records(destination))
        await asyncio.sleep(0.02)
        await send({"type": "http.response.body", "body": b"final", "more_body": False})

    async def request(identifier):
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await TimingMiddleware(app)(
            {
                "type": "http",
                "method": "POST",
                "path": "/" + identifier,
                "headers": [(b"x-request-id", identifier.encode())],
            },
            receive,
            send,
        )
        assert (b"x-request-id", identifier.encode()) in messages[0]["headers"]

    await asyncio.gather(request("one"), request("two"))
    rows = records(destination)
    for identifier in ("one", "two"):
        worker_rows = [row for row in rows if row["stage"] == "worker" and row["request_id"] == identifier]
        assert len(worker_rows) == 2
        assert all(row["run_id"] for row in worker_rows)
        http = next(row for row in rows if row["stage"] == "http" and row["request_id"] == identifier)
        assert http["elapsed_ms"] >= 20
        assert http["complete"] is True
        assert all(row["parent_span_id"] == http["span_id"] for row in worker_rows)


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_tracing_disabled_and_exception_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("PERFORMANCE_TRACE_FILE", raising=False)
    with span("disabled"):
        pass
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("PERFORMANCE_TRACE_FILE", str(tmp_path / "trace.jsonl"))
    with pytest.raises(ValueError, match="original"), span("failing"):
        raise ValueError("original")
    assert records(tmp_path / "trace.jsonl")[0]["outcome"] == "error"


def test_failed_parse_without_duration_is_retained(tmp_path, monkeypatch):
    destination = tmp_path / "trace.jsonl"
    monkeypatch.setenv("PERFORMANCE_TRACE_FILE", str(destination))
    with span("node"):
        log_event(
            logging.getLogger(__name__),
            logging.WARNING,
            "llm.output.parse.fail",
            scene="normalization",
            error_type="ValueError",
            query="must-not-be-recorded",
        )
    diagnostic = next(row for row in records(destination) if row["kind"] == "diagnostic")
    assert diagnostic["outcome"] == "error"
    assert "elapsed_ms" not in diagnostic
    assert "must-not-be-recorded" not in destination.read_text()

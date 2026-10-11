import asyncio
import subprocess
import sys
import time

import httpx
import pytest

from evaluation.performance_metrics import SSEParser, summarize
from evaluation.performance_runner import (
    execute_case,
    load_manifest,
    managed_server,
    read_trace,
    request_stream,
    write_reports,
)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    output = tmp_path_factory.mktemp("performance") / "http"
    cases = []
    with (
        managed_server(output, offline=True) as (url, setup),
        httpx.Client(
            timeout=20,
            trust_env=False,
        ) as client,
    ):
        yield url, client, output, cases
    with pytest.raises(httpx.HTTPError):
        httpx.get(url + "/health", timeout=0.5, trust_env=False)
    write_reports(
        output,
        {
            "mode": "offline",
            "status": "passed" if all(case["success"] for case in cases) else "failed",
            "rounds": [
                {
                    "setup": setup,
                    "cases": cases,
                    "summary": summarize(cases),
                    "trace": read_trace(output / "spans.jsonl"),
                }
            ],
        },
    )


@pytest.mark.parametrize("case", load_manifest()["cases"], ids=lambda case: case["id"])
def test_fixed_http_scenarios(server, case):
    url, client, output, cases = server
    result = execute_case(client, url, case, output)
    cases.append(result)
    assert result["success"], result.get("error")
    assert result["scenario_ms"] >= 0
    assert len(result["requests"]) == (2 if case["group"] == "hitl" else 1)
    if case["group"] == "memory":
        assert result["ttft_ms"] is None
        assert result["preparations"]
    if case["group"] in {"rag", "hitl"}:
        request_ids = {request["request_id"] for request in result["requests"]}
        stages = {row["stage"] for row in read_trace(output / "spans.jsonl") if row.get("request_id") in request_ids}
        assert {"search.vector.embedding.done", "search.vector.query.done", "retrieval.rerank"} <= stages


def test_model_failure_is_not_accepted_as_success(server):
    url, client, output, _ = server
    result = execute_case(
        client,
        url,
        {
            "id": "model-failure",
            "group": "direct",
            "query": "Hello __perf_fail__",
            "expect_token": True,
        },
        output,
    )
    assert not result["success"]
    assert "missing_answer_token" in result["error"]


def test_http_stream_timeout_is_failure_even_with_http_200(server):
    url, client, _, _ = server
    observation, measurement = request_stream(
        client,
        url,
        "/api/v1/recommend/stream",
        {
            "query": "Hello __perf_slow__",
            "session_id": "timeout-http",
            "timeout": 10,
            "hitl_policy": "auto_confirm",
        },
        "timeout-http",
    )
    assert measurement["http_status"] == 200
    with pytest.raises(ValueError, match="timeout"):
        observation.validate()
    assert measurement["elapsed_ms"] < 15000


@pytest.mark.asyncio
async def test_capacity_and_disconnect_cleanup(server):
    url, _, output, _ = server
    # Construct the shared client before the occupancy deadline. Client setup is
    # synchronous and can otherwise consume the timer while creating four clients.
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        started = [asyncio.Event() for _ in range(4)]
        release = asyncio.Event()

        async def occupy(index):
            parser = SSEParser()
            async with client.stream(
                "POST",
                url + "/api/v1/recommend/stream",
                json={"query": "Hello __perf_hold__", "session_id": f"capacity-{index}", "hitl_policy": "auto_confirm"},
                headers={"X-Request-Id": f"capacity-{index}"},
            ) as response:
                async for chunk in response.aiter_bytes():
                    for event, payload in parser.feed(chunk):
                        if event == "node" and payload.get("node") == "routing":
                            started[index].set()
                            await release.wait()
                            return

        tasks = [asyncio.create_task(occupy(index)) for index in range(4)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=10)
            response = await client.post(
                url + "/api/v1/recommend/stream",
                json={
                    "query": "Hello",
                    "session_id": "capacity-overflow",
                    "hitl_policy": "auto_confirm",
                },
            )
            events = SSEParser().feed(response.content, final=True)
            assert any(event == "error" for event, _ in events)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        # Cancellation must release the workers and retain the original request ID.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            records = read_trace(output / "spans.jsonl")
            workers = [
                row
                for row in records
                if row.get("stage") == "node.chat_answer_generation_node"
                and str(row.get("request_id", "")).startswith("capacity-")
            ]
            cleanups = [row for row in records if row.get("stage") == "execution.cleanup"
                        and str(row.get("request_id", "")).startswith("capacity-")]
            if len(workers) == 4 and len(cleanups) == 4:
                break
            await asyncio.sleep(0.05)
        assert len(workers) == 4
        assert all(row["outcome"] == "error" for row in workers)
        assert len(cleanups) == 4
        assert all(row["outcome"] == "success" for row in cleanups)
        response = await client.post(
            url + "/api/v1/recommend/stream",
            json={
                "query": "Hello",
                "session_id": "after-cancel",
                "hitl_policy": "auto_confirm",
            },
        )
        assert any(event == "final" for event, _ in SSEParser().feed(response.content, final=True))


def test_offline_guard_prevents_remote_dns():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from evaluation.offline_support import block_external_network; import socket; "
                "block_external_network(); socket.getaddrinfo('example.com',443)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "prohibit external" in result.stderr

import asyncio
import threading
import time

import pytest

from software_recommend_system.execution_control import (
    RunStopped,
    SessionRunConflict,
    run_controlled,
    stream_controlled,
)


@pytest.mark.asyncio
async def test_run_timeout_is_reported_and_worker_releases_session():
    started_at = time.monotonic()
    with pytest.raises(RunStopped) as stopped:
        await run_controlled(
            lambda control: time.sleep(0.2),
            session_id="timeout-test",
            timeout_seconds=0.03,
        )

    assert stopped.value.stop_reason == "timeout"
    assert time.monotonic() - started_at < 0.2
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            _, control = await run_controlled(
                lambda _: "ok", session_id="timeout-test", timeout_seconds=1
            )
            assert control.elapsed_ms < 1000
            return
        except SessionRunConflict:
            await asyncio.sleep(0.01)
    pytest.fail("timed-out worker did not release its session")


@pytest.mark.asyncio
async def test_same_session_conflict_and_caller_cancellation():
    started = threading.Event()
    observed_stop = threading.Event()

    def wait_for_stop(control):
        started.set()
        control.stop_event.wait(1)
        observed_stop.set()

    task = asyncio.create_task(
        run_controlled(
            wait_for_stop, session_id="same-session-test", timeout_seconds=2
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    with pytest.raises(SessionRunConflict):
        await run_controlled(
            lambda _: None, session_id="same-session-test", timeout_seconds=1
        )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(observed_stop.wait, 1)


@pytest.mark.asyncio
async def test_stream_completion_yields_terminal_control():
    observed = []
    terminal_control = None

    async for item, _control in stream_controlled(
        lambda _: iter(["first", "second"]),
        session_id="stream-success-test",
        timeout_seconds=1,
    ):
        observed.append(item)
        terminal_control = _control

    assert observed[:2] == ["first", "second"]
    assert observed[2] is None
    assert terminal_control is not None
    assert terminal_control.stop_reason is None


@pytest.mark.asyncio
async def test_cancelling_stream_sets_cooperative_stop_signal():
    started = threading.Event()
    stopped = threading.Event()

    def source(control):
        started.set()
        try:
            while not control.stop_event.wait(0.01):
                yield "tick"
        finally:
            stopped.set()

    async def consume():
        async for _item, _control in stream_controlled(
            source, session_id="stream-cancel-test", timeout_seconds=2
        ):
            await asyncio.sleep(0.01)

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(stopped.wait, 1)

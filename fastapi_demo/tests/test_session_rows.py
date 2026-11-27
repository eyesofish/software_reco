import json
import logging
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from app.api.v1 import session_store as sessions
from software_recommend_system import memory_store as memory
from software_recommend_system.config import settings
from software_recommend_system.session_rows import open_session_rows


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    chat = tmp_path / "sessions.json"
    monkeypatch.setattr(settings, "SESSION_STATE_FILE", str(chat))
    monkeypatch.setattr(settings, "SESSION_STATE_DB", None)
    monkeypatch.setattr(settings, "MEMORY_STORE_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(settings, "MEMORY_STORE_DB", None)
    monkeypatch.setattr(settings, "FEATURE_LAYERED_MEMORY", True)
    yield chat
    open_session_rows.cache_clear()


def test_migration_once_and_reopen_preserve_both_stores(legacy, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_MAX_MESSAGES", 2)
    chat = {"facts": {"user_name": "Alice"}, "messages": [{"role": "user", "content": str(i)} for i in range(3)]}
    legacy.write_text(json.dumps({"alice": chat, "bob": {"facts": {"language": "中文"}}}), encoding="utf-8")
    original = legacy.read_bytes()
    legacy.with_name("memory.json").write_text(json.dumps({"alice": {"facts": {"project": "SQLite"}}}))
    rows = sessions.session_rows()
    before = sessions.peek_session_state("bob")
    assert sessions.peek_session_state("alice")["messages"] == chat["messages"][-2:]
    assert memory.get_facts("alice") == {"project": "SQLite"}
    with closing(sqlite3.connect(rows.path)) as db:
        assert db.execute("SELECT session_count FROM session_imports").fetchone() == (2,)
    sessions._merge_session_facts("alice", {"user_name": "Ada"})
    sessions._append_session_message_once("alice", "assistant", "complete")
    open_session_rows.cache_clear()
    assert sessions.peek_session_state("alice")["facts"] == {"user_name": "Ada"}
    assert sessions.peek_session_state("alice")["messages"][0]["content"] == "2"
    assert sessions.peek_session_state("bob") == before
    assert memory.get_facts("alice") == {"project": "SQLite", "user_name": "Ada"}
    assert legacy.read_bytes() == original
    monkeypatch.setattr(sessions, "SESSION_MAX_MESSAGES", 1)
    assert sessions._get_or_create_session_state("alice")["messages"] == [{"role": "assistant", "content": "complete"}]


@pytest.mark.parametrize(
    "bad",
    [
        b'{"broken":',
        b'{"first":{"facts":{}},"second":null}',
        b'{" a ":{}, "a":{}}',
        b'{"a":{}, "a":{}}',
        b'{"a":{"facts":[]}}',
    ],
)
def test_invalid_migration_rolls_back_and_leaves_source_for_retry(legacy, bad):
    legacy.write_bytes(bad)
    with pytest.raises(RuntimeError, match="Session migration failed"):
        sessions.session_rows()
    assert legacy.read_bytes() == bad
    with closing(sqlite3.connect(legacy.with_suffix(".sqlite3"))) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='agent_session_state'").fetchone()
    legacy.write_text('{"first":{}, "second":{}}', encoding="utf-8")
    assert sessions.session_rows().read("second") is not None


def test_real_sqlite_rollback_and_concurrent_read_modify_write(legacy, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_ENABLE_WRITEBACK", False)
    rows = sessions.session_rows()
    sessions._merge_session_facts("owner", {"before": "original"})
    with pytest.raises(ValueError, match="abort"), rows.edit("owner") as state:
        state["facts"].clear()
        raise ValueError("abort")
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda i: sessions._merge_session_facts("owner", {str(i): "saved"}), range(12)))
        list(workers.map(lambda _: sessions._append_session_message_once("owner", "assistant", "one"), range(4)))
    assert rows.read("owner")["facts"] == {"before": "original", **{str(i): "saved" for i in range(12)}}
    assert rows.read("owner")["messages"] == [{"role": "assistant", "content": "one"}]


def test_fresh_process_reads_committed_conversation_and_memory(legacy):
    env = {
        **os.environ,
        "SESSION_STATE_FILE": str(legacy),
        "SESSION_STATE_DB": str(legacy.with_suffix(".sqlite3")),
        "MEMORY_STORE_FILE": str(legacy.with_name("memory.json")),
        "MEMORY_STORE_DB": str(legacy.with_name("memory.sqlite3")),
        "FEATURE_LAYERED_MEMORY": "true",
        "MEMORY_ENABLE_WRITEBACK": "true",
    }
    imports = (
        "from app.api.v1.session_store import _merge_session_facts, _append_session_messages, peek_session_state\n"
        "from software_recommend_system.memory_store import get_facts\n"
    )
    write = (
        "_merge_session_facts('original-id', {'user_name': 'Ada'})\n"
        "_append_session_messages('original-id', [{'role': 'assistant', 'content': 'committed'}])"
    )
    read = (
        "assert peek_session_state('original-id')['messages'][0]['content'] == 'committed'\n"
        "assert get_facts('original-id') == {'user_name': 'Ada'}"
    )
    subprocess.run([sys.executable, "-c", imports + write], env=env, check=True, timeout=30)
    subprocess.run([sys.executable, "-c", imports + read], env=env, check=True, timeout=30)


def test_update_touches_one_of_ten_thousand_real_sessions(legacy):
    rows = sessions.session_rows()
    initial = '{"facts":{}, "messages":[], "updated_at":1}'
    with closing(sqlite3.connect(rows.path)) as db, db:
        db.executemany("INSERT INTO agent_session_state VALUES (?, ?)", ((str(i), initial) for i in range(10_000)))
        db.execute("CREATE TABLE changed(id TEXT)")
        db.execute(
            "CREATE TRIGGER audit AFTER UPDATE ON agent_session_state "
            "BEGIN INSERT INTO changed VALUES(new.session_id); END"
        )
        query = db.execute("EXPLAIN QUERY PLAN SELECT state_json FROM agent_session_state WHERE session_id=?", ("42",))
        assert "SEARCH" in query.fetchone()[3]
    sessions._append_session_messages("42", [{"role": "user", "content": "hello"}])
    assert not sessions.peek_session_state("missing")
    with closing(sqlite3.connect(rows.path)) as db:
        assert db.execute("SELECT id FROM changed").fetchall() == [("42",)]
        assert db.execute("SELECT COUNT(*) FROM agent_session_state").fetchone() == (10_000,)
        unchanged = db.execute("SELECT COUNT(*) FROM agent_session_state WHERE state_json=?", (initial,)).fetchone()
        assert unchanged == (9999,)


async def test_stream_logs_keep_session_id_without_query_history_or_failure_text(legacy, monkeypatch, caplog):
    from app.api.v1 import routes
    from app.api.v1.models import RecommendationRequest
    from software_recommend_system.logging_utils import log_event

    async def allow(request):
        return None

    async def prepare(request):
        return "test-kb", request.query, request.query, [], []

    marker = "CHAT_PRIVATE_SENTINEL_372"
    monkeypatch.setattr(routes, "preflight", allow)
    monkeypatch.setattr(routes, "_prepare_pinned_request", prepare)
    monkeypatch.setattr(settings, "FEATURE_LAYERED_MEMORY", False)
    sessions._merge_session_facts("log-test", {"user_name": "Alice"})
    request = RecommendationRequest(query=f"what is my name? {marker}", session_id="log-test")
    with caplog.at_level(logging.INFO):
        response = await routes.stream_software_recommendation(request)
        frames = "".join([frame async for frame in response.body_iterator])
        assert "event: final" in frames
        log_event(logging.getLogger("retrieval"), logging.INFO, "retrieval.search", query=marker, docs=1)

        async def fail(request):
            raise ValueError(marker)

        monkeypatch.setattr(routes, "_prepare_pinned_request", fail)
        failed = await routes.stream_software_recommendation(request)
        errors = "".join([frame async for frame in failed.body_iterator])
        assert "event: error" in errors and marker not in errors
    assert marker not in caplog.text
    assert "log-test" in caplog.text and "ValueError" in caplog.text

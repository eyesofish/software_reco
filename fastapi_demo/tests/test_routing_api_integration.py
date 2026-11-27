import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.v1 import routing_api as api
from app.api.v1.models import RecommendationRequest
from software_recommend_system.intent_routing import RoutingResolution, RoutingSnapshot


def settings(**changes):
    values = {
        "JEV_TIMEOUT_SECONDS": 8,
        "ROUTING_DEPLOYMENT_MODE": "live",
        "ROUTING_TRAFFIC_PERCENT": 100,
        "ROUTING_AUTO_SESSION_APPROVED": True,
        "ROUTING_GATE_REPORT": "missing-report.json",
        "DYNAMIC_ROUTING_ENABLE": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_unresolved_route_needs_evidence_not_only_operator_latch(monkeypatch):
    monkeypatch.setattr(api, "settings", settings())
    resolver = Mock(side_effect=AssertionError("not approved"))
    monkeypatch.setattr(api, "resolve_routing", resolver)
    response = await api.routing_resolve(RoutingSnapshot(query="continue", current_conversation_id="s1"))
    assert response.status == "routing_clarification"
    resolver.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_timeout_is_bounded_and_retains_structured_target(monkeypatch):
    monkeypatch.setattr(api, "settings", settings(JEV_TIMEOUT_SECONDS=0.02))

    def slow(snapshot):
        time.sleep(0.15)
        return RoutingResolution(snapshot_id=snapshot.snapshot_id, target="existing", target_session_id="wrong")

    monkeypatch.setattr(api, "resolve_routing", slow)
    started = time.monotonic()
    response = await api.bounded_resolve(RoutingSnapshot(query="q", binding="existing", target_session_id="fixed"))
    assert time.monotonic() - started < 0.12
    assert response.target_session_id == "fixed"
    assert response.mode == "rag"
    assert response.source == "fallback"


@pytest.mark.asyncio
async def test_preflight_cancellation_does_not_turn_into_rag(monkeypatch):
    monkeypatch.setattr(api, "settings", settings())
    monkeypatch.setattr(api, "resolve_routing", lambda snapshot: time.sleep(0.1))
    task = asyncio.create_task(api.bounded_resolve(RoutingSnapshot(binding="new", query="q")))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cohort_uses_same_unsigned_sha256_bucket_as_java(monkeypatch):
    monkeypatch.setattr(api, "settings", settings(ROUTING_TRAFFIC_PERCENT=3))
    assert not api._in_cohort("owner-1:session-1")
    monkeypatch.setattr(api, "settings", settings(ROUTING_TRAFFIC_PERCENT=4))
    assert api._in_cohort("owner-1:session-1")
    assert not api._in_cohort("owner-1:new")


@pytest.mark.asyncio
async def test_multimodal_keeps_captured_kb_when_publication_changes(monkeypatch, tmp_path):
    from app.api.v1 import routes
    from software_recommend_system import kb_versions as kb

    monkeypatch.setattr(kb, "manifest_path", lambda: tmp_path / "manifest.sqlite3")
    store = kb.get_store()
    store.adopt_legacy(lambda: {})
    published = store.stage({})
    seen = []

    async def prepare(_request):
        seen.append(kb.current_snapshot_id())
        store.publish(published)
        seen.append(await asyncio.to_thread(kb.current_snapshot_id))
        await asyncio.sleep(0.01)
        return "q", "q", [], []

    monkeypatch.setattr(routes, "_prepare_multimodal_request", prepare)
    request = RecommendationRequest(query="q", execution_budget_seconds=1)
    pinned, *prepared = await routes._prepare_pinned_request(request)
    assert pinned == "legacy"
    assert seen == ["legacy", "legacy"]
    assert kb.capture_snapshot() == published
    assert kb.current_snapshot_id() is None
    assert prepared == ["q", "q", [], []]
    assert 0 < request.execution_budget_seconds < 1


@pytest.mark.asyncio
async def test_multimodal_preparation_obeys_remaining_deadline(monkeypatch, tmp_path):
    from app.api.v1 import routes
    from software_recommend_system import kb_versions as kb

    monkeypatch.setattr(kb, "manifest_path", lambda: tmp_path / "manifest.sqlite3")

    async def prepare(_request):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(routes, "_prepare_multimodal_request", prepare)
    with pytest.raises(HTTPException) as stopped:
        await routes._prepare_pinned_request(RecommendationRequest(query="q", execution_budget_seconds=0.01))
    assert stopped.value.status_code == 408
    assert kb.current_snapshot_id() is None

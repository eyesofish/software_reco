import threading

from software_recommend_system.intent_routing import RoutingResolution, RoutingSnapshot
from software_recommend_system.routing_shadow import RoutingShadow


def test_shadow_freezes_snapshot_and_drops_without_blocking():
    entered, release = threading.Event(), threading.Event()
    seen, records = [], []

    def resolver(snapshot, **kwargs):
        entered.set()
        release.wait(2)
        seen.append(snapshot)
        return RoutingResolution(snapshot_id=snapshot["snapshot_id"], target="existing", target_session_id="s1")

    worker = RoutingShadow(capacity=1, resolver=resolver, sink=records.append)
    first = RoutingSnapshot(snapshot_id="one", query="original", binding="existing", target_session_id="s1")
    assert worker.submit(first)
    assert entered.wait(1)
    first.query = "changed after submission"
    assert worker.submit(RoutingSnapshot(snapshot_id="two"))
    assert not worker.submit(RoutingSnapshot(snapshot_id="three"))
    release.set()
    worker.close()
    assert seen[0]["query"] == "original"
    assert worker.statistics()["dropped"] == 1
    assert worker.statistics()["completed"] == 2
    assert any(row["outcome"] == "dropped" for row in records)
    assert all(row["shadow"] for row in records)
    assert not worker.submit(first)


def test_failure_and_sink_failure_do_not_escape_into_caller():
    def resolver(*args, **kwargs):
        raise RuntimeError("failure")

    def sink(record):
        raise OSError("full")

    worker = RoutingShadow(resolver=resolver, sink=sink)
    assert worker.submit(RoutingSnapshot())
    worker.close()
    assert worker.statistics()["failed"] == 1
    assert worker.statistics()["sink_failures"] == 1

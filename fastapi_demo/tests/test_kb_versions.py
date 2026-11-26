from software_recommend_system import kb_versions as kb


def test_manifest_recovers_first_initialization_after_schema_only_commit(tmp_path):
    path = tmp_path / "initializing.sqlite3"
    store = kb.ManifestStore(path)
    # SQLite DDL can be visible before the first control-row transaction commits.
    with store.connection() as db:
        db.execute("DELETE FROM control")
        db.execute("DELETE FROM snapshots")
    recovered = kb.ManifestStore(path)
    assert recovered.active() == kb.LEGACY
    assert recovered.members(kb.LEGACY) is None

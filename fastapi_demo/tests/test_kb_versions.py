"""Offline MVCC tests: local Chroma/SQLite and deterministic embeddings."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import chromadb
import pytest
from chromadb.api.types import EmbeddingFunction

from software_recommend_system import kb_versions as kb
from software_recommend_system import keyword_index, tools
from software_recommend_system.config import settings
from software_recommend_system.ingestion import embedder, indexer


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


class ConstantEmbedding(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input):
        return [[1.0, 0.0] for _ in input]

    @staticmethod
    def name():
        return "mvcc-test-constant"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return ConstantEmbedding()


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    for name, value in {
        "CHROMA_DB_PATH": str(tmp_path / "chroma"),
        "INGEST_PATH": str(root),
        "RECALL_ENABLE_KEYWORD": False,
        "RECALL_ENABLE_IMAGE_VECTOR": False,
        "CHUNKING_STRATEGY": "sentence",
        "ENABLE_PARENT_CHILD_CHUNKING": False,
        "CHUNK_SIZE": 80,
        "CHUNK_OVERLAP": 10,
        "PARENT_CHUNK_SIZE": 100,
        "PARENT_CHUNK_OVERLAP": 10,
        "CHILD_CHUNK_SIZE": 50,
        "CHILD_CHUNK_OVERLAP": 5,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "KB_MANIFEST_PATH", str(tmp_path / "manifest.sqlite3"), raising=False)
    client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
    text = client.create_collection(indexer.DEFAULT_COLLECTION_NAME, embedding_function=ConstantEmbedding())
    parent = client.create_collection(settings.PARENT_COLLECTION_NAME, embedding_function=ConstantEmbedding())
    image = client.create_collection(indexer.resolve_image_collection_name(), embedding_function=ConstantEmbedding())
    monkeypatch.setattr(indexer, "get_chroma_collection", lambda **kw: text)
    monkeypatch.setattr(indexer, "get_parent_collection", lambda *args, **kw: parent)
    monkeypatch.setattr(indexer, "get_image_collection", lambda *args, **kw: image)
    embed = Mock(side_effect=lambda values: [[1.0, 0.0] for _ in values])
    monkeypatch.setattr(embedder, "embed_texts", embed)
    monkeypatch.setattr(tools, "embed_texts", embed)
    return SimpleNamespace(root=root, text=text, parent=parent, image=image, embed=embed)


def doc(identity="a", content="Original text."):
    return {"id": identity, "content": content, "metadata": {"source": "test", "path": identity + ".txt"}}


def selected(collection, snapshot):
    with kb.use_snapshot(snapshot):
        return collection.get(where=kb.chroma_filter(), include=["documents", "metadatas"])


def test_incremental_manifests_keep_old_and_reuse_unchanged_rows(store):
    first = indexer.publish_documents([doc(), doc("b", "Shared document.")])["snapshot_id"]
    before = selected(store.text, first)
    second = indexer.publish_documents([doc(content="New revision.")])["snapshot_id"]
    old, current = selected(store.text, first), selected(store.text, second)
    assert old == before
    assert set(old["documents"]) == {"Original text.", "Shared document."}
    assert set(current["documents"]) == {"New revision.", "Shared document."}
    assert next(meta for meta in current["metadatas"] if meta["document_id"] == "b")["snapshot_id"] == first
    assert store.text.count() == 3
    assert indexer.publish_documents([doc(content="New revision.")])["documents"] == 0
    assert all(all(meta.get(key) for key in kb.VERSION_FIELDS) for meta in current["metadatas"])


def test_failed_stage_and_missing_receipts_never_publish(store, monkeypatch):
    first = indexer.publish_documents([doc()])["snapshot_id"]
    monkeypatch.setattr(settings, "RECALL_ENABLE_KEYWORD", True)
    keyword = SimpleNamespace(upsert=Mock(side_effect=ConnectionError("ES offline")))
    monkeypatch.setattr(keyword_index, "get_keyword_index", lambda: keyword)
    with pytest.raises(ConnectionError, match="ES offline"):
        indexer.publish_documents([doc(content="Unpublished new row.")])
    assert kb.capture_snapshot() == first
    assert selected(store.text, first)["documents"] == ["Original text."]
    assert store.text.count() == 2
    manifest = kb.get_store()
    staged = manifest.stage({"a": "other"}, {"a": {"text", "image"}})
    manifest.mark_ready(staged, "a", "text")
    with pytest.raises(RuntimeError, match="not ready"):
        manifest.publish(staged)
    for invalid in (staged, "unknown"):
        with pytest.raises(kb.SnapshotUnavailable), kb.use_snapshot(invalid):
            pass


def test_concurrent_publications_and_rollback_only_change_new_pins(store):
    first = indexer.publish_documents([doc()])["snapshot_id"]
    manifest, barrier = kb.get_store(), Barrier(2)

    def publish(identity):
        staged = manifest.stage({identity: "v1"}, expected_base=first)
        barrier.wait()
        try:
            return manifest.publish(staged)
        except kb.PublicationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("b", "c")))
    assert results.count("conflict") == 1
    published = next(value for value in results if value != "conflict")
    with kb.use_snapshot(published):
        kb.rollback_snapshot(first)
        assert kb.current_snapshot_id() == published
        assert kb.capture_snapshot() == first
    assert len(manifest.members(published)) == 2


def test_adoption_keeps_legacy_rows_and_excludes_failed_stage(store, monkeypatch):
    store.text.add(
        ids=["legacy:0"],
        documents=["Existing original."],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"source": "old", "source_doc_id": "legacy", "path": "legacy.txt"}],
    )
    assert kb.capture_snapshot() == kb.LEGACY
    with kb.use_snapshot(kb.LEGACY):
        assert kb.chroma_filter() is None
    original_write = indexer.index_embeddings

    def write_then_fail(*args, **kwargs):
        original_write(*args, **kwargs)
        raise RuntimeError("index write interrupted")

    monkeypatch.setattr(indexer, "index_embeddings", write_then_fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        indexer.publish_documents([doc()])
    assert kb.capture_snapshot() == kb.LEGACY
    old = selected(store.text, kb.LEGACY)
    assert old["ids"] == ["legacy:0"]
    assert old["documents"] == ["Existing original."]
    assert old["metadatas"][0]["version"].startswith("legacy-")
    monkeypatch.setattr(indexer, "index_embeddings", original_write)
    current = indexer.publish_documents([doc()])["snapshot_id"]
    assert set(selected(store.text, current)["documents"]) == {"Existing original.", "Original text."}
    assert selected(store.text, kb.LEGACY) == old


def test_parent_lookup_cannot_expand_across_revisions(store, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_PARENT_CHILD_CHUNKING", True)
    first = indexer.publish_documents([doc(content="Old parent sentence. " * 12)])["snapshot_id"]
    old_ids = {meta["parent_id"] for meta in selected(store.text, first)["metadatas"]}
    second = indexer.publish_documents([doc(content="New parent sentence. " * 12)])["snapshot_id"]
    with kb.use_snapshot(first):
        parents = indexer.get_parent_documents_by_ids(list(old_ids))
        assert parents and all("Old" in item["content"] for item in parents.values())
    with kb.use_snapshot(second):
        assert indexer.get_parent_documents_by_ids(list(old_ids)) == {}
    assert len(selected(store.parent, first)["ids"]) == len(old_ids)


def test_image_assets_and_vectors_stay_immutable(store, monkeypatch):
    from software_recommend_system import image_embedder

    monkeypatch.setattr(settings, "RECALL_ENABLE_IMAGE_VECTOR", True)
    monkeypatch.setattr(image_embedder, "embed_image_files", lambda paths: [[1.0, 0.0] for _ in paths])
    asset = store.root / "screen.png"
    asset.write_bytes(b"old image bytes")
    original = doc("screen", "Old caption.")
    original["metadata"].update(modality="image", media_type="image/png")
    first = indexer.publish_documents([original], image_sources={"screen": asset})["snapshot_id"]
    old = selected(store.image, first)
    old_path = store.root / old["metadatas"][0]["asset_path"]
    asset.write_bytes(b"new image bytes")
    changed = doc("screen", "New caption.")
    changed["metadata"].update(modality="image", media_type="image/png")
    second = indexer.publish_documents([changed], image_sources={"screen": asset})["snapshot_id"]
    current = selected(store.image, second)
    assert old["ids"] != current["ids"]
    assert old_path.read_bytes() == b"old image bytes"
    assert (store.root / current["metadatas"][0]["asset_path"]).read_bytes() == b"new image bytes"
    assert selected(store.image, first) == old and store.image.count() == 2
    from app.api.v1.startup_ingest import _list_candidate_files

    assert _list_candidate_files(store.root) == [asset]


def test_keyword_and_text_queries_enforce_membership(store):
    first = indexer.publish_documents([doc()])["snapshot_id"]
    second = indexer.publish_documents([doc(content="Changed answer.")])["snapshot_id"]
    with kb.use_snapshot(first):
        results = tools.similarity_search("anything", k=2)
        assert [item.content for item in results] == ["Original text."]
        assert results[0].metadata.snapshot_id == first
        client = Mock()
        old, new = selected(store.text, first), selected(store.text, second)
        client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "old",
                        "_score": 1,
                        "_source": {"content": "Original text.", "metadata": old["metadatas"][0]},
                    },
                    {
                        "_id": "new",
                        "_score": 2,
                        "_source": {"content": "Changed answer.", "metadata": new["metadatas"][0]},
                    },
                ]
            }
        }
        keyword = keyword_index.KeywordIndex(client, "test")
        assert [item.content for item in keyword.search("anything", 5)] == ["Original text."]
        assert "filter" in client.search.call_args.kwargs["query"]["bool"]
    with pytest.raises(kb.SnapshotUnavailable, match="pin"):
        kb.chroma_filter()

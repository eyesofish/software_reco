import json
from unittest.mock import Mock

import chromadb
import pytest
from chromadb.api.types import EmbeddingFunction

from software_recommend_system import keyword_index
from software_recommend_system.config import settings
from software_recommend_system.ingestion import embedder, indexer, rechunk


class ConstantEmbedding(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input):
        return [[1.0, 0.0] for _ in input]

    @staticmethod
    def name():
        return "rechunk-test-constant"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return ConstantEmbedding()


class MemoryKeyword:
    def __init__(self):
        self.rows = {}
        self.fail = False

    def upsert(self, ids, texts, metadatas):
        if self.fail:
            raise ConnectionError("test ES unavailable")
        self.rows.update({key: (text, meta) for key, text, meta in zip(ids, texts, metadatas, strict=True)})

    def delete(self, ids):
        for key in ids:
            self.rows.pop(key, None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    monkeypatch.setattr(settings, "INGEST_PATH", str(root))
    monkeypatch.setattr(settings, "CHUNKING_STRATEGY", "structure")
    monkeypatch.setattr(settings, "ENABLE_PARENT_CHILD_CHUNKING", False)
    monkeypatch.setattr(settings, "RECALL_ENABLE_KEYWORD", True)
    monkeypatch.setattr(settings, "CHUNK_SIZE", 90)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 10)
    monkeypatch.setattr(settings, "PARENT_CHUNK_SIZE", 140)
    monkeypatch.setattr(settings, "PARENT_CHUNK_OVERLAP", 20)
    monkeypatch.setattr(settings, "CHILD_CHUNK_SIZE", 65)
    monkeypatch.setattr(settings, "CHILD_CHUNK_OVERLAP", 10)
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    text = client.create_collection("rechunk_text", embedding_function=ConstantEmbedding())
    parent = client.create_collection("rechunk_parent", embedding_function=ConstantEmbedding())
    monkeypatch.setattr(indexer, "get_chroma_collection", lambda **kwargs: text)
    monkeypatch.setattr(indexer, "get_parent_collection", lambda *args, **kwargs: parent)
    embed = Mock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(embedder, "embed_texts", embed)
    keyword = MemoryKeyword()
    monkeypatch.setattr(keyword_index, "get_keyword_index", lambda: keyword)
    return root, text, parent, keyword, embed


def write_doc(root, name="guide.md", body=None):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or ("# A\n\n" + "A useful sentence. " * 40 + "\n\n# B\nLast."), encoding="utf-8")
    return rechunk.load_document(path, root)


def test_preview_is_offline_and_never_opens_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "INGEST_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "CHUNKING_STRATEGY", "structure")
    write_doc(tmp_path)
    forbidden = Mock(side_effect=AssertionError("preview must not access indexes or embeddings"))
    monkeypatch.setattr(indexer, "get_chroma_collection", forbidden)
    monkeypatch.setattr(indexer, "get_parent_collection", forbidden)
    monkeypatch.setattr(embedder, "embed_texts", forbidden)
    monkeypatch.setattr(keyword_index, "get_keyword_index", forbidden)
    assert rechunk.main(["--path", "guide.md"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview" and result["chunks"] > 0
    forbidden.assert_not_called()


def test_rebuild_shrinks_and_preserves_identity_and_is_idempotent(store):
    root, text, parent, keyword, embed = store
    doc = write_doc(root)
    # Legacy identity may differ from the new path-derived identity.
    doc["id"] = "legacy-id"
    first = rechunk.apply_document(doc)
    assert first["chunks"] > 1
    doc = write_doc(root, body="# Small\nA short replacement.")
    result = rechunk.apply_document(doc)
    assert result["removed_chunks"] > 0 and text.count() == 1
    assert text.get()["ids"] == ["legacy-id:0"]
    assert set(keyword.rows) == {"legacy-id:0"}
    assert rechunk.apply_document(doc)["removed_chunks"] == 0
    assert text.count() == 1 and parent.count() == 0


def test_switch_parent_child_both_directions(store, monkeypatch):
    root, text, parent, keyword, _ = store
    doc = write_doc(root)
    rechunk.apply_document(doc)
    monkeypatch.setattr(settings, "ENABLE_PARENT_CHILD_CHUNKING", True)
    rechunk.apply_document(doc)
    assert parent.count() > 0
    assert all(":c:" in key for key in text.get()["ids"])
    assert set(keyword.rows) == set(text.get()["ids"])
    monkeypatch.setattr(settings, "ENABLE_PARENT_CHILD_CHUNKING", False)
    rechunk.apply_document(doc)
    assert parent.count() == 0
    assert all(":c:" not in key for key in text.get()["ids"])
    assert set(keyword.rows) == set(text.get()["ids"])


def test_embedding_failure_preserves_old_records(store):
    root, text, _, keyword, embed = store
    rechunk.apply_document(write_doc(root))
    before = text.get()
    old_keyword = dict(keyword.rows)
    embed.side_effect = RuntimeError("embedding failed")
    with pytest.raises(RuntimeError, match="embedding failed"):
        rechunk.apply_document(write_doc(root, body="# Changed\nDifferent."))
    assert text.get() == before and keyword.rows == old_keyword


def test_es_failure_retains_stale_ids_until_retry(store):
    root, text, _, keyword, _ = store
    rechunk.apply_document(write_doc(root))
    before_ids = set(text.get()["ids"])
    keyword.fail = True
    doc = write_doc(root, body="# Small\nReplacement.")
    with pytest.raises(RuntimeError, match="ES sync failed"):
        rechunk.apply_document(doc)
    assert set(text.get()["ids"]) == before_ids
    keyword.fail = False
    rechunk.apply_document(doc)
    assert text.count() == 1 and set(keyword.rows) == set(text.get()["ids"])


def test_same_basename_in_different_directories_never_overwrites(store):
    root, text, _, keyword, _ = store
    first = write_doc(root, "one/guide.md", "# First\nOne.")
    second = write_doc(root, "two/guide.md", "# Second\nTwo.")
    assert first["id"] != second["id"]
    rechunk.apply_document(first)
    rechunk.apply_document(second)
    assert text.count() == 2
    assert {m["path"] for m in text.get()["metadatas"]} == {"one/guide.md", "two/guide.md"}


def test_collision_refused_before_embedding(store):
    root, text, _, _, embed = store
    text.upsert(
        ids=["guide.md:0"],
        documents=["other"],
        embeddings=[[1, 0]],
        metadatas=[{"path": "other.md", "source_doc_id": "guide.md"}],
    )
    with pytest.raises(ValueError, match="another source"):
        rechunk.apply_document(write_doc(root))
    embed.assert_not_called()
    assert text.get()["documents"] == ["other"]


def test_invalid_paths_and_unreadable_pdf_return_failure_without_writes(store, capsys):
    root, text, _, _, embed = store
    outside = root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    assert rechunk.main(["--path", "../outside.md", "--apply"]) == 1
    (root / "bad.pdf").write_bytes(b"invalid PDF")
    assert rechunk.main(["--path", "bad.pdf", "--apply"]) == 1
    assert text.count() == 0
    embed.assert_not_called()


def test_write_failure_returns_nonzero_and_retry_cleans_up(store, monkeypatch):
    root, text, _, keyword, _ = store
    write_doc(root)
    original = indexer.index_embeddings
    failure = Mock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr(indexer, "index_embeddings", failure)
    assert rechunk.main(["--path", "guide.md", "--apply"]) == 1
    assert text.count() == 0
    monkeypatch.setattr(indexer, "index_embeddings", original)
    assert rechunk.main(["--path", "guide.md", "--apply"]) == 0
    assert text.count() > 0 and set(text.get()["ids"]) == set(keyword.rows)

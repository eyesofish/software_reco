from unittest.mock import Mock

import pytest

from software_recommend_system import retriever
from software_recommend_system.document_schema import Document, Metadata


def doc(identifier, text, score=0.9):
    return Document(content=text, score=score, metadata=Metadata(source="vector", doc_id=identifier))


@pytest.fixture(autouse=True)
def local_channels(monkeypatch):
    for field, value in {"RECALL_ENABLE_VECTOR": True, "RECALL_ENABLE_KEYWORD": True,
                         "RECALL_ENABLE_WEB": False, "RECALL_ENABLE_MEMORY": False,
                         "RECALL_ENABLE_IMAGE_VECTOR": False, "RETRIEVAL_ENABLE_RERANK": False}.items():
        monkeypatch.setattr(retriever.settings, field, value)


def test_original_and_additive_query_run_both_local_channels(monkeypatch):
    query = "Redis 7.2 不使用云服务，3个节点"
    expansion = query + "\ncache"
    vector = Mock(side_effect=lambda **kw: [doc("vector-new" if kw["query"] == expansion else "vector-old",
                                               query if kw["query"] == expansion else "unrelated")])
    keyword = Mock(side_effect=lambda **kw: [doc("keyword-new" if kw["query"] == expansion else "keyword-old",
                                                query if kw["query"] == expansion else "unrelated")])
    monkeypatch.setattr(retriever, "recall_vector", vector)
    monkeypatch.setattr(retriever, "recall_keyword", keyword)
    result = retriever.retrieve(query, rewrite_query=expansion, defer_rerank=True)
    assert {call.kwargs["query"] for call in vector.call_args_list} == {query, expansion}
    assert {call.kwargs["query"] for call in keyword.call_args_list} == {query, expansion}
    assert {item.metadata.doc_id for item in result} == {
        "vector-old", "vector-new", "keyword-old", "keyword-new",
    }


def test_query_dependent_score_cannot_fake_rewrite_improvement(monkeypatch):
    query = "Redis persistence durability"
    expansion = query + "\ncache"
    vector = Mock(side_effect=lambda **kw: [doc("new", "unrelated", 100) if kw["query"] == expansion
                                           else doc("old", query, 0.01)])
    monkeypatch.setattr(retriever, "recall_vector", vector)
    monkeypatch.setattr(retriever, "recall_keyword", lambda **kw: [])
    result = retriever.retrieve(query, rewrite_query=expansion, defer_rerank=True)
    assert [item.metadata.doc_id for item in result] == ["old"]


def test_replacement_query_is_not_run(monkeypatch):
    vector = Mock(return_value=[doc("old", "Redis 7.2")])
    monkeypatch.setattr(retriever, "recall_vector", vector)
    monkeypatch.setattr(retriever, "recall_keyword", lambda **kw: [])
    retriever.retrieve("Redis 7.2", rewrite_query="Redis 8.0", defer_rerank=True)
    vector.assert_called_once()
    assert vector.call_args.kwargs["query"] == "Redis 7.2"

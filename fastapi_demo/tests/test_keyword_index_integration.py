"""Run with RUN_ES_INTEGRATION=1; only a fresh UUID-named test index is mutated."""

import os
import unittest
import uuid

import chromadb
from elasticsearch import Elasticsearch

from software_recommend_system.keyword_index import KeywordIndex


@unittest.skipUnless(os.environ.get("RUN_ES_INTEGRATION") == "1", "Requires running Elasticsearch")
class ElasticsearchIntegrationTests(unittest.TestCase):
    def test_full_corpus_search_update_prune_and_repeat_sync(self) -> None:
        client = Elasticsearch(
            os.environ.get("ES_TEST_URL", "http://127.0.0.1:9200"), request_timeout=10, max_retries=0
        )
        name = "software_reco_eval_test_" + uuid.uuid4().hex
        keyword = KeywordIndex(client, name)
        source_client = chromadb.EphemeralClient()
        source_name = "test_" + uuid.uuid4().hex
        collection = source_client.create_collection(source_name)
        try:
            ids = [f"chunk_{i}" for i in range(2501)]
            texts = ["general unrelated software documentation"] * 2500 + ["Redis 内存数据库连接失败 ERR_CONN_9981"]
            metadatas = [{"source": "test", "source_doc_id": str(i)} for i in range(2501)]
            metadatas[-1]["tags"] = "unique_tag_2501,缓存"
            collection.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=[[1.0, 0.0]] * 2501)
            self.assertEqual(keyword.reconcile(collection)["indexed"], 2501)
            for query in ["Redis", "数据库连接失败", "ERR_CONN_9981", "unique_tag_2501"]:
                docs = keyword.search(query, 3)
                self.assertTrue(docs, query)
                self.assertEqual(docs[0].metadata.doc_id, "2500", query)
            collection.upsert(
                ids=["chunk_2500"],
                documents=["PostgreSQL transaction isolation"],
                metadatas=[{"source": "test", "source_doc_id": "2500"}],
                embeddings=[[1.0, 0.0]],
            )
            collection.delete(ids=["chunk_0"])
            self.assertEqual(keyword.reconcile(collection), {"indexed": 2500, "deleted": 1, "failed": 0})
            self.assertEqual(keyword.search("ERR_CONN_9981", 3), [])
            self.assertEqual(keyword.search("PostgreSQL", 3)[0].metadata.doc_id, "2500")
            self.assertEqual(keyword.reconcile(collection)["deleted"], 0)
            self.assertEqual(client.count(index=name)["count"], 2500)
            keyword.delete(["absent-id"])
        finally:
            client.indices.delete(index=name, ignore_unavailable=True)
            client.close()
            source_client.delete_collection(source_name)

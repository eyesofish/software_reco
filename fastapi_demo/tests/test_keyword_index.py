import unittest
from unittest.mock import MagicMock, patch

from evaluation.keyword_index_config import validate_keyword_index
from software_recommend_system import keyword_index
from software_recommend_system.ingestion import indexer, sync_keyword_index
from software_recommend_system.keyword_index import KeywordIndex


class KeywordIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.index = KeywordIndex(self.client, "software_reco_eval_unit")
        keyword_index._WRITE_UNAVAILABLE_UNTIL = 0.0

    def tearDown(self) -> None:
        keyword_index._WRITE_UNAVAILABLE_UNTIL = 0.0

    def test_search_preserves_source_id_and_image_metadata(self) -> None:
        self.client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "chunk:1",
                        "_score": 3.5,
                        "_source": {
                            "content": "Redis screenshot",
                            "metadata": {
                                "source": "startup_ingest",
                                "doc_id": "chunk:1",
                                "source_doc_id": "image.png",
                                "tags": "redis;cache",
                                "modality": "image",
                                "asset_path": "image.png",
                            },
                        },
                    }
                ]
            }
        }
        docs = self.index.search("Redis", 4)
        self.assertEqual(docs[0].metadata.doc_id, "image.png")
        self.assertEqual(docs[0].metadata.asset_path, "image.png")
        self.assertEqual(docs[0].metadata.tags, ["redis", "cache"])
        self.assertEqual(docs[0].score, 3.5)
        self.assertEqual(self.client.search.call_args.kwargs["size"], 4)

    def test_search_rejects_partial_timeout(self) -> None:
        self.client.search.return_value = {"timed_out": True, "hits": {"hits": []}}
        with self.assertRaises(TimeoutError):
            self.index.search("redis", 4)

    def test_upsert_uses_stable_id_and_cjk_mapping(self) -> None:
        self.client.indices.exists.return_value = False
        with patch.object(keyword_index.helpers, "bulk", return_value=(1, [])) as bulk:
            self.index.upsert(["chunk:1"], ["内容"], [{"tags": "redis,缓存"}])
        mappings = self.client.indices.create.call_args.kwargs["mappings"]
        self.assertEqual(mappings["properties"]["content"]["analyzer"], "cjk")
        action = bulk.call_args.args[1][0]
        self.assertEqual(action["_id"], "chunk:1")
        self.assertEqual(action["_source"]["tags"], ["redis", "缓存"])

    def test_reconciliation_pages_all_documents_before_pruning(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 501
        collection.get.side_effect = [
            {"ids": [str(i) for i in range(500)], "documents": ["text"] * 500, "metadatas": [{}] * 500},
            {"ids": ["500"], "documents": ["last"], "metadatas": [{}]},
        ]
        with (
            patch.object(self.index, "upsert", side_effect=[500, 1]) as upsert,
            patch.object(
                self.index,
                "delete",
                return_value=1,
            ) as delete,
            patch.object(keyword_index.helpers, "scan", return_value=[{"_id": "0"}, {"_id": "old"}]),
        ):
            result = self.index.reconcile(collection)
        self.assertEqual(result, {"indexed": 501, "deleted": 1, "failed": 0})
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(collection.get.call_args.kwargs["offset"], 500)
        delete.assert_called_once_with(["old"])

    def test_partial_bulk_failure_never_prunes(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 2
        collection.get.return_value = {"ids": ["a", "b"], "documents": ["a", "b"], "metadatas": [{}, {}]}
        with (
            patch.object(keyword_index.helpers, "bulk", return_value=(1, [{"index": {"status": 400}}])),
            patch.object(
                self.index,
                "delete",
            ) as delete,
            self.assertRaisesRegex(RuntimeError, "bulk failed"),
        ):
            self.index.reconcile(collection)
        delete.assert_not_called()

    def test_read_failure_or_changing_source_never_prunes(self) -> None:
        for page in [{"ids": [], "documents": [], "metadatas": []}, {"ids": ["a"], "documents": [], "metadatas": []}]:
            collection = MagicMock()
            collection.count.return_value = 2
            collection.get.return_value = page
            with patch.object(self.index, "delete") as delete, self.assertRaises((ValueError, RuntimeError)):
                self.index.reconcile(collection)
            delete.assert_not_called()

    def test_scan_failure_never_prunes(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 0
        with (
            patch.object(keyword_index.helpers, "scan", side_effect=RuntimeError("scan failed")),
            patch.object(
                self.index,
                "delete",
            ) as delete,
            self.assertRaisesRegex(RuntimeError, "scan failed"),
        ):
            self.index.reconcile(collection)
        delete.assert_not_called()

    def test_source_write_succeeds_even_if_es_is_down(self) -> None:
        collection = MagicMock()
        with (
            patch.object(indexer, "get_chroma_collection", return_value=collection),
            patch.object(
                keyword_index,
                "get_keyword_index",
                side_effect=TimeoutError("ES down"),
            ) as get_index,
            patch.object(keyword_index.settings, "RECALL_ENABLE_KEYWORD", True),
        ):
            self.assertEqual(indexer.index_embeddings(["a"], ["text"], [{}], [[1.0, 0.0]]), 1)
            self.assertEqual(indexer.index_embeddings(["b"], ["text"], [{}], [[1.0, 0.0]]), 1)
        self.assertEqual(collection.upsert.call_count, 2)
        get_index.assert_called_once()

    def test_disabled_or_image_benchmark_writes_do_not_connect(self) -> None:
        with (
            patch.object(indexer, "get_chroma_collection"),
            patch.object(keyword_index, "get_keyword_index") as get_index,
        ):
            indexer.index_embeddings(["a"], ["text"], [{}], [[1.0]], sync_keyword=False)
            with patch.object(keyword_index.settings, "RECALL_ENABLE_KEYWORD", False):
                indexer.index_embeddings(["a"], ["text"], [{}], [[1.0]])
                sync_keyword_index.sync_keyword_index_on_startup()
        get_index.assert_not_called()

    def test_startup_repairs_existing_collection_without_ingest_files(self) -> None:
        collection = MagicMock()
        with (
            patch.object(sync_keyword_index.chromadb, "PersistentClient") as client,
            patch.object(
                sync_keyword_index,
                "get_keyword_index",
                return_value=self.index,
            ),
            patch.object(self.index, "reconcile", return_value={"indexed": 3}) as reconcile,
        ):
            client.return_value.get_collection.return_value = collection
            self.assertEqual(sync_keyword_index.sync_keyword_index(), {"indexed": 3})
            client.return_value.get_or_create_collection.assert_not_called()
            reconcile.assert_called_once_with(collection)

    def test_startup_failure_is_nonfatal_but_cli_exits_nonzero(self) -> None:
        with (
            patch.object(sync_keyword_index, "sync_keyword_index", side_effect=RuntimeError("ES down")),
            patch.object(
                keyword_index.settings,
                "RECALL_ENABLE_KEYWORD",
                True,
            ),
        ):
            sync_keyword_index.sync_keyword_index_on_startup()
            with self.assertRaises(SystemExit) as caught:
                sync_keyword_index.main()
        self.assertEqual(caught.exception.code, 1)

    def test_evaluation_rejects_business_index_and_wildcards(self) -> None:
        for name in ["software_recommendations_keyword_v1", "software_reco_eval_*", "other", ""]:
            with self.assertRaises(ValueError):
                validate_keyword_index(name)
        self.assertEqual(validate_keyword_index("software_reco_eval_test"), "software_reco_eval_test")

import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from PIL import Image

from software_recommend_system import (
    image_embedder,
    retrieval_channels,
    retriever,
)
from software_recommend_system.document_schema import Document
from software_recommend_system.ingestion import indexer


def _one_pixel_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def _two_pixel_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 1), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeImageModel:
    def __init__(self) -> None:
        self.inputs = []

    def encode(self, inputs, **_):
        self.inputs = list(inputs)
        return [[0.6, 0.8] for _ in self.inputs]


class ImageEmbeddingTests(unittest.TestCase):
    def test_shared_encoder_accepts_text_and_image_inputs(self) -> None:
        model = _FakeImageModel()
        with patch.object(image_embedder, "_get_image_model", return_value=model):
            text_vectors = image_embedder.embed_image_texts(["architecture diagram"])
            image_vectors = image_embedder.embed_image_bytes([_one_pixel_png()])

        self.assertEqual(text_vectors, [[0.6, 0.8]])
        self.assertEqual(image_vectors, [[0.6, 0.8]])

    def test_single_flat_vector_is_normalized_to_one_row(self) -> None:
        self.assertEqual(
            image_embedder._convert_vectors([0.6, 0.8], expected_count=1),
            [[0.6, 0.8]],
        )

    def test_image_file_embedding_enforces_configured_byte_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "oversized.png"
            image_path.write_bytes(b"x" * 11)
            with patch.object(
                image_embedder.settings,
                "MULTIMODAL_MAX_IMAGE_BYTES",
                10,
            ), self.assertRaises(image_embedder.ImageEmbeddingError):
                image_embedder.embed_image_files([image_path])

    def test_image_embedding_enforces_configured_pixel_limit(self) -> None:
        with patch.object(
            image_embedder.settings,
            "MULTIMODAL_MAX_IMAGE_PIXELS",
            1,
        ), self.assertRaisesRegex(
            image_embedder.ImageEmbeddingError,
            "pixel embedding limit",
        ):
            image_embedder.embed_image_bytes([_two_pixel_png()])


class ImageCollectionTests(unittest.TestCase):
    def test_configured_collection_is_versioned_by_embedding_identity(self) -> None:
        with patch.object(
            indexer,
            "image_embedding_identity",
            return_value="clip-model@revision-a",
        ):
            first_name = indexer.resolve_image_collection_name()
        with patch.object(
            indexer,
            "image_embedding_identity",
            return_value="clip-model@revision-b",
        ):
            second_name = indexer.resolve_image_collection_name()

        self.assertNotEqual(first_name, second_name)
        self.assertTrue(first_name.startswith("software_recommendations_image_"))

    def test_image_collection_uses_cosine_distance(self) -> None:
        client = MagicMock()
        with patch.object(indexer.chromadb, "PersistentClient", return_value=client):
            indexer.get_image_collection("knowledge_images")

        client.get_or_create_collection.assert_called_once_with(
            "knowledge_images",
            metadata={"hnsw:space": "cosine"},
        )

    def test_image_embeddings_are_upserted_with_captions(self) -> None:
        collection = MagicMock()
        with patch.object(indexer, "get_image_collection", return_value=collection):
            count = indexer.index_image_embeddings(
                image_ids=["diagram.png"],
                captions=["A service architecture diagram."],
                metadatas=[{"path": "diagram.png", "modality": "image"}],
                embeddings=[[0.1, 0.2]],
            )

        self.assertEqual(count, 1)
        collection.upsert.assert_called_once_with(
            ids=["diagram.png"],
            documents=["A service architecture diagram."],
            metadatas=[{"path": "diagram.png", "modality": "image"}],
            embeddings=[[0.1, 0.2]],
        )


class ImageVectorRecallTests(unittest.TestCase):
    def test_text_and_image_queries_are_rrf_fused(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 2
        collection.query.return_value = {
            "ids": [
                ["diagram.png", "settings.png"],
                ["diagram.png", "settings.png"],
            ],
            "documents": [
                ["Architecture diagram", "Settings screen"],
                ["Architecture diagram", "Settings screen"],
            ],
            "metadatas": [
                [
                    {
                        "source": "startup_ingest",
                        "source_doc_id": "diagram.png",
                        "filename": "diagram.png",
                        "media_type": "image/png",
                        "modality": "image",
                        "asset_path": "diagram.png",
                    },
                    {
                        "source": "startup_ingest",
                        "source_doc_id": "settings.png",
                        "filename": "settings.png",
                        "media_type": "image/png",
                        "modality": "image",
                        "asset_path": "settings.png",
                    },
                ],
                [
                    {
                        "source": "startup_ingest",
                        "source_doc_id": "diagram.png",
                        "filename": "diagram.png",
                        "media_type": "image/png",
                        "modality": "image",
                        "asset_path": "diagram.png",
                    },
                    {
                        "source": "startup_ingest",
                        "source_doc_id": "settings.png",
                        "filename": "settings.png",
                        "media_type": "image/png",
                        "modality": "image",
                        "asset_path": "settings.png",
                    },
                ],
            ],
            "distances": [[0.1, 0.4], [0.05, 0.3]],
        }

        with patch.object(
            retrieval_channels,
            "embed_image_texts",
            return_value=[[1.0, 0.0]],
        ), patch.object(
            retrieval_channels,
            "get_image_collection",
            return_value=collection,
        ):
            docs = retrieval_channels.recall_image_vector(
                "service architecture",
                top_k=2,
                query_image_embeddings=[[0.0, 1.0]],
                trace_id="test-image-vector",
            )

        self.assertEqual([doc.metadata.doc_id for doc in docs], ["diagram.png", "settings.png"])
        self.assertEqual(docs[0].metadata.query_modalities, ["image", "text"])
        self.assertEqual(docs[0].metadata.asset_path, "diagram.png")
        self.assertGreater(docs[0].score, docs[1].score)
        collection.query.assert_called_once_with(
            query_embeddings=[[1.0, 0.0], [0.0, 1.0]],
            n_results=2,
            include=["documents", "metadatas", "distances"],
        )

    def test_checkpointed_image_candidates_fuse_with_text_query(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 1
        collection.query.return_value = {
            "ids": [["diagram.png"]],
            "documents": [["Current architecture diagram caption"]],
            "metadatas": [
                [
                    {
                        "source": "startup_ingest",
                        "source_doc_id": "diagram.png",
                        "filename": "diagram.png",
                        "media_type": "image/png",
                        "modality": "image",
                        "asset_path": "diagram.png",
                    }
                ]
            ],
            "distances": [[0.1]],
        }
        seed = Document(
            content="Image-to-image candidate captured before HITL.",
            metadata={
                "source": "image_vector",
                "doc_id": "diagram.png",
                "filename": "diagram.png",
                "media_type": "image/png",
                "modality": "image",
                "asset_path": "diagram.png",
                "query_modalities": ["image"],
            },
            score=1.0,
        )

        with patch.object(
            retrieval_channels,
            "embed_image_texts",
            return_value=[[1.0, 0.0]],
        ), patch.object(
            retrieval_channels,
            "get_image_collection",
            return_value=collection,
        ):
            docs = retrieval_channels.recall_image_vector(
                "service architecture",
                top_k=2,
                seed_documents=[seed],
                trace_id="test-image-seed",
            )

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.query_modalities, ["image", "text"])
        self.assertEqual(docs[0].metadata.asset_path, "diagram.png")
        self.assertEqual(docs[0].content, "Current architecture diagram caption")


class DualRouteFusionTests(unittest.TestCase):
    def _doc(
        self,
        doc_id: str,
        *,
        source: str,
        score: float,
        asset_path: str | None = None,
    ) -> Document:
        return Document(
            content=f"Content for {doc_id}",
            metadata={
                "source": source,
                "doc_id": doc_id,
                "filename": doc_id if asset_path else None,
                "media_type": "image/png" if asset_path else None,
                "modality": "image" if asset_path else None,
                "asset_path": asset_path,
            },
            score=score,
        )

    def test_local_rrf_merges_duplicate_routes_and_preserves_asset_metadata(self) -> None:
        vector_docs = retriever._normalize_documents(
            [
                self._doc("diagram.png", source="vector", score=0.2),
                self._doc("text-only", source="vector", score=0.3),
            ],
            source_hint="vector",
            channel="vector",
        )
        image_docs = retriever._normalize_documents(
            [
                self._doc(
                    "diagram.png",
                    source="image_vector",
                    score=0.9,
                    asset_path="diagram.png",
                ),
                self._doc(
                    "visual-only.png",
                    source="image_vector",
                    score=0.8,
                    asset_path="visual-only.png",
                ),
            ],
            source_hint="image_vector",
            channel="image_vector",
        )

        fused = retriever._fuse_local_vector_documents(vector_docs, image_docs)
        by_id = {doc.metadata.doc_id: doc for doc in fused}

        self.assertEqual(
            by_id["diagram.png"].metadata.matched_channels,
            ["image_vector", "vector"],
        )
        self.assertEqual(by_id["diagram.png"].metadata.asset_path, "diagram.png")
        self.assertEqual(
            by_id["diagram.png"].metadata.retrieval_source,
            "multimodal_vector",
        )
        self.assertGreater(
            by_id["diagram.png"].score,
            by_id["visual-only.png"].score,
        )

    def test_local_rrf_counts_each_document_once_per_channel(self) -> None:
        first_vector_doc = self._doc("diagram.png", source="vector", score=0.1)
        duplicate_vector_doc = self._doc("diagram.png", source="vector", score=0.2)
        image_doc = self._doc(
            "visual-only.png",
            source="image_vector",
            score=0.9,
            asset_path="visual-only.png",
        )

        baseline = retriever._fuse_local_vector_documents(
            retriever._normalize_documents(
                [first_vector_doc],
                source_hint="vector",
                channel="vector",
            ),
            retriever._normalize_documents(
                [image_doc],
                source_hint="image_vector",
                channel="image_vector",
            ),
        )
        with_duplicate = retriever._fuse_local_vector_documents(
            retriever._normalize_documents(
                [first_vector_doc, duplicate_vector_doc],
                source_hint="vector",
                channel="vector",
            ),
            retriever._normalize_documents(
                [image_doc],
                source_hint="image_vector",
                channel="image_vector",
            ),
        )

        baseline_score = {
            doc.metadata.doc_id: doc.score for doc in baseline
        }["diagram.png"]
        duplicate_score = {
            doc.metadata.doc_id: doc.score for doc in with_duplicate
        }["diagram.png"]
        self.assertEqual(duplicate_score, baseline_score)

    def test_retrieve_executes_native_image_channel(self) -> None:
        vector_doc = self._doc("diagram.png", source="vector", score=0.2)
        image_doc = self._doc(
            "diagram.png",
            source="image_vector",
            score=0.9,
            asset_path="diagram.png",
        )

        with patch.object(retriever.settings, "RECALL_ENABLE_VECTOR", True), patch.object(
            retriever.settings,
            "RECALL_ENABLE_IMAGE_VECTOR",
            True,
        ), patch.object(retriever.settings, "RECALL_ENABLE_WEB", False), patch.object(
            retriever.settings,
            "RECALL_ENABLE_KEYWORD",
            False,
        ), patch.object(retriever.settings, "RECALL_ENABLE_MEMORY", False), patch.object(
            retriever.settings,
            "RETRIEVAL_ENABLE_RERANK",
            False,
        ), patch.object(
            retriever,
            "recall_vector",
            return_value=[vector_doc],
        ), patch.object(
            retriever,
            "recall_image_vector",
            return_value=[image_doc],
        ) as mocked_image_recall:
            docs = retriever.retrieve(
                "architecture diagram",
                query_image_candidates=[image_doc],
            )

        mocked_image_recall.assert_called_once()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.asset_path, "diagram.png")
        self.assertEqual(
            docs[0].metadata.matched_channels,
            ["image_vector", "vector"],
        )


if __name__ == "__main__":
    unittest.main()

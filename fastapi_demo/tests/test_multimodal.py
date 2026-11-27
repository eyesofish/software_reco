from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1 import startup_ingest
from app.api.v1.handlers.normalizers import _extract_eval_payload
from app.api.v1.models import ImageAttachment, RecommendationRequest
from app.api.v1.routes import (
    _prepare_multimodal_request,
    _resolve_ingested_asset,
)
from software_recommend_system import multimodal
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.llm_utils import _build_retrieved_images
from software_recommend_system.state import AgentState


def _png_data_url(payload_size: int = 16) -> str:
    raw = b"\x89PNG\r\n\x1a\n" + (b"x" * payload_size)
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class RecommendationRequestMultimodalTests(unittest.TestCase):
    def test_image_only_request_is_valid(self) -> None:
        request = RecommendationRequest(
            images=[
                ImageAttachment(
                    name="error.png",
                    media_type="image/png",
                    data_url=_png_data_url(),
                )
            ]
        )

        self.assertEqual(request.query, "")
        self.assertEqual(request.images[0].name, "error.png")

    def test_request_requires_text_or_image(self) -> None:
        with self.assertRaises(ValidationError):
            RecommendationRequest()

    def test_attachment_header_must_match_media_type(self) -> None:
        with self.assertRaises(ValidationError):
            ImageAttachment(
                name="error.png",
                media_type="image/jpeg",
                data_url=_png_data_url(),
            )


class MultimodalProcessingTests(unittest.TestCase):
    def test_decode_rejects_spoofed_media_type(self) -> None:
        with self.assertRaises(multimodal.MultimodalInputError):
            multimodal.decode_image_data_url(
                _png_data_url(),
                expected_media_type="image/jpeg",
                max_bytes=1024,
            )

    def test_describe_attachments_validates_and_captions(self) -> None:
        attachment = ImageAttachment(
            name="diagram.png",
            media_type="image/png",
            data_url=_png_data_url(),
        )
        with patch.object(
            multimodal,
            "caption_image_data_url",
            return_value="A service diagram with API and vector database nodes.",
        ) as mocked_caption:
            result = multimodal.describe_image_attachments(
                [attachment],
                user_query="Explain this architecture",
            )

        self.assertEqual(result[0]["name"], "diagram.png")
        self.assertIn("vector database", result[0]["description"])
        mocked_caption.assert_called_once()

    def test_caption_uses_openai_multimodal_message(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = {
            "choices": [{"message": {"content": "Visible error: connection refused."}}]
        }
        with patch.object(multimodal, "_get_vision_client", return_value=client):
            caption = multimodal.caption_image_data_url(
                name="error.png",
                media_type="image/png",
                data_url=_png_data_url(),
                user_query="Why did this fail?",
            )

        self.assertEqual(caption, "Visible error: connection refused.")
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        user_content = messages[1]["content"]
        self.assertEqual(user_content[1]["type"], "image_url")
        self.assertEqual(user_content[1]["image_url"]["url"], _png_data_url())

    def test_query_includes_visual_context(self) -> None:
        query = multimodal.build_multimodal_query(
            "How do I fix this?",
            [
                {
                    "name": "error.png",
                    "media_type": "image/png",
                    "description": "Terminal shows connection refused on port 5432.",
                }
            ],
        )

        self.assertIn("How do I fix this?", query)
        self.assertIn("connection refused", query)

    def test_local_image_preflights_size_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "oversized.png"
            image_path.write_bytes(b"x" * 11)
            with (
                patch.object(
                    multimodal.settings,
                    "MULTIMODAL_MAX_IMAGE_BYTES",
                    10,
                ),
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("read_bytes should not be called"),
                ),
                self.assertRaises(multimodal.MultimodalInputError),
            ):
                multimodal.describe_local_image(image_path)

    def test_local_image_wraps_read_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "unreadable.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
            with (
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=PermissionError("permission denied"),
                ),
                self.assertRaises(multimodal.MultimodalInputError),
            ):
                multimodal.describe_local_image(image_path)


class MultimodalRequestPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_image_candidates_are_checkpoint_safe(self) -> None:
        request = RecommendationRequest(
            query="Explain this",
            images=[
                ImageAttachment(
                    name="diagram.png",
                    media_type="image/png",
                    data_url=_png_data_url(),
                )
            ],
        )
        descriptions = [
            {
                "name": "diagram.png",
                "media_type": "image/png",
                "description": "A service architecture diagram.",
            }
        ]
        candidate = Document(
            content="Architecture diagram showing an API and vector database.",
            metadata=Metadata(
                source="image_vector",
                doc_id="knowledge/diagram.png",
                filename="diagram.png",
                media_type="image/png",
                modality="image",
                asset_path="knowledge/diagram.png",
            ),
            score=1.0,
        )
        with (
            patch(
                "app.api.v1.routes.describe_image_attachments",
                return_value=descriptions,
            ),
            patch(
                "app.api.v1.routes.embed_image_attachments",
                return_value=[[0.1, 0.2]],
            ),
            patch(
                "app.api.v1.routes.recall_image_vector",
                return_value=[candidate],
            ),
            patch(
                "app.api.v1.routes.settings.RECALL_ENABLE_IMAGE_VECTOR",
                True,
            ),
        ):
            effective_query, _, prepared_descriptions, candidates = await _prepare_multimodal_request(request)

        self.assertIn("service architecture", effective_query)
        self.assertEqual(prepared_descriptions, descriptions)
        self.assertEqual(candidates, [candidate])
        state = AgentState(
            user_query=effective_query,
            input_images=prepared_descriptions,
            query_image_candidates=candidates,
        )
        self.assertNotIn("query_image_embeddings", state.model_dump())
        self.assertNotIn("query_image_embedding_key", state.model_dump())
        restored = AgentState.model_validate(state.model_dump())
        self.assertEqual(
            restored.query_image_candidates[0].metadata.doc_id,
            "knowledge/diagram.png",
        )


class RetrievedImageTests(unittest.TestCase):
    def test_image_metadata_becomes_api_citation(self) -> None:
        document = Document(
            content="Architecture diagram showing a React client calling FastAPI.",
            metadata=Metadata(
                source="startup_ingest",
                doc_id="diagrams/system.png",
                filename="system.png",
                media_type="image/png",
                modality="image",
                asset_path="diagrams/system.png",
            ),
            score=0.12,
        )

        images = _build_retrieved_images([document])
        self.assertEqual(images[0]["doc_id"], "diagrams/system.png")
        self.assertEqual(images[0]["url"], "/api/v1/assets/diagrams/system.png")

        payload = _extract_eval_payload(
            {
                "retrieved_doc_ids": ["diagrams/system.png"],
                "retrieved_images": images,
            }
        )
        self.assertEqual(payload["retrieved_images"][0]["filename"], "system.png")


class StartupImageIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        for name, value in {
            "CHROMA_DB_PATH": str(Path(storage.name) / "chroma"),
            "KB_MANIFEST_PATH": str(Path(storage.name) / "manifest.sqlite3"),
        }.items():
            patcher = patch.object(startup_ingest.settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_read_indexed_content_rebuilds_overlapping_chunks(self) -> None:
        collection = MagicMock()
        collection.get.return_value = {
            "documents": ["efghij", "abcdef"],
            "metadatas": [
                {"chunk_start": 4, "chunk_end": 10},
                {"chunk_start": 0, "chunk_end": 6},
            ],
        }

        content = startup_ingest._read_indexed_content(
            collection,
            "diagrams/ui.png",
        )

        self.assertEqual(content, "abcdefghij")
        collection.get.assert_called_once_with(
            where={"path": "diagrams/ui.png"},
            include=["documents", "metadatas"],
        )

    def test_read_image_builds_searchable_caption_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "ui.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
            with patch.object(
                startup_ingest,
                "describe_local_image",
                return_value={
                    "name": "ui.png",
                    "media_type": "image/png",
                    "description": "A settings screen with an API key field.",
                },
            ):
                content, metadata = startup_ingest._read_file_content(image_path)

        self.assertIn("API key field", content)
        self.assertEqual(metadata["modality"], "image")
        self.assertEqual(metadata["media_type"], "image/png")

    def test_source_publication_bundles_caption_asset_and_native_vector(self) -> None:
        from software_recommend_system.ingestion import indexer

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "ui.png"
            image.write_bytes(b"image bytes")
            raw = {"id": "ui.png", "content": "Current screen.", "metadata": {"path": "ui.png", "modality": "image"}}
            with (
                patch.object(startup_ingest.settings, "INGEST_PATH", str(root)),
                patch.object(indexer, "publish_documents", return_value={"chunks": 2, "images": 1}) as publish,
            ):
                self.assertEqual(startup_ingest._ingest_single_document(raw), 2)
            publish.assert_called_once_with([raw], image_sources={"ui.png": image}, ingest_root=str(root))

    def test_changed_image_uses_fresh_caption_and_one_atomic_publication(self) -> None:
        collection = MagicMock()
        collection.get.return_value = {"ids": ["old:0"], "metadatas": [{"source_sha256": "old"}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "ui.png"
            image.write_bytes(b"new bytes")
            with (
                patch.object(startup_ingest.settings, "INGEST_PATH", str(root)),
                patch.object(startup_ingest, "get_chroma_collection", return_value=collection),
                patch.object(
                    startup_ingest,
                    "describe_local_image",
                    return_value={"description": "New screen.", "media_type": "image/png"},
                ),
                patch.object(startup_ingest, "_ingest_single_document", return_value=1) as publish,
            ):
                startup_ingest.run_startup_ingestion_if_needed()
            publish.assert_called_once()
            raw = publish.call_args.args[0]
            self.assertIn("New screen", raw["content"])
            self.assertEqual(raw["metadata"]["source_sha256"], startup_ingest._image_file_sha256(image))
            collection.delete.assert_not_called()

    def test_caption_failure_keeps_existing_rows_and_never_publishes_partial_image(self) -> None:
        from software_recommend_system.multimodal import VisionProcessingError

        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "ui.png").write_bytes(b"new bytes")
                collection = MagicMock()
                collection.get.return_value = {
                    "ids": ["old"] if existing else [],
                    "metadatas": [{"source_sha256": "old"}] if existing else [],
                }
                with (
                    patch.object(startup_ingest.settings, "INGEST_PATH", str(root)),
                    patch.object(startup_ingest, "get_chroma_collection", return_value=collection),
                    patch.object(startup_ingest, "describe_local_image", side_effect=VisionProcessingError("offline")),
                    patch.object(startup_ingest, "_ingest_single_document") as publish,
                ):
                    startup_ingest.run_startup_ingestion_if_needed()
                publish.assert_not_called()
                collection.delete.assert_not_called()

    def test_identical_image_bytes_reuse_cached_caption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "ui.png"
            image.write_bytes(b"same bytes")
            digest = startup_ingest._image_file_sha256(image)
            collection = MagicMock()
            collection.get.return_value = {
                "ids": ["old"],
                "documents": ["Cached screen."],
                "metadatas": [{"source_sha256": digest}],
            }
            with (
                patch.object(startup_ingest.settings, "INGEST_PATH", str(root)),
                patch.object(startup_ingest, "get_chroma_collection", return_value=collection),
                patch.object(startup_ingest, "describe_local_image") as describe,
                patch.object(startup_ingest, "_ingest_single_document", return_value=0) as publish,
            ):
                startup_ingest.run_startup_ingestion_if_needed()
            describe.assert_not_called()
            self.assertEqual(publish.call_args.args[0]["content"], "Cached screen.")

    def test_text_updates_also_publish_instead_of_skipping_indexed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "guide.txt").write_text("Updated text", encoding="utf-8")
            with (
                patch.object(startup_ingest.settings, "INGEST_PATH", str(root)),
                patch.object(startup_ingest, "_ingest_single_document", return_value=1) as publish,
            ):
                startup_ingest.run_startup_ingestion_if_needed()
            self.assertEqual(publish.call_args.args[0]["content"], "Updated text")


class AssetResolutionTests(unittest.TestCase):
    def test_asset_resolution_stays_inside_ingest_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "diagrams" / "system.png"
            image_path.parent.mkdir()
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
            with patch("app.api.v1.routes.settings.INGEST_PATH", str(root)):
                resolved, media_type = _resolve_ingested_asset("diagrams/system.png")
                self.assertEqual(resolved, image_path.resolve())
                self.assertEqual(media_type, "image/png")
                with self.assertRaises(HTTPException):
                    _resolve_ingested_asset("../outside.png")


if __name__ == "__main__":
    unittest.main()

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
from app.api.v1.routes import _resolve_ingested_asset
from software_recommend_system import multimodal
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.llm_utils import _build_retrieved_images


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

    def test_existing_vector_store_still_ingests_new_files(self) -> None:
        collection = MagicMock()
        collection.count.return_value = 10
        collection.get.return_value = {"ids": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "new.txt").write_text("new content", encoding="utf-8")
            with patch.object(startup_ingest.settings, "INGEST_PATH", str(root)), patch.object(
                startup_ingest,
                "get_chroma_collection",
                return_value=collection,
            ), patch.object(
                startup_ingest,
                "_ingest_single_document",
                return_value=1,
            ) as mocked_ingest:
                startup_ingest.run_startup_ingestion_if_needed()

        mocked_ingest.assert_called_once()


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

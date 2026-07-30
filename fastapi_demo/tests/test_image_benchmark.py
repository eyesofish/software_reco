import copy
import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from evaluation import image_benchmark


class ImageBenchmarkManifestTests(unittest.TestCase):
    def test_manifest_has_expected_assets_queries_and_consensus(self) -> None:
        manifest = image_benchmark.load_manifest()

        self.assertEqual(
            Counter(asset["category"] for asset in manifest["assets"]),
            {"architecture": 12, "ui": 10, "error": 8},
        )
        self.assertEqual(
            Counter(query["modality"] for query in manifest["queries"]),
            {"text": 12, "image": 6},
        )
        for query in manifest["queries"]:
            self.assertEqual(query["label_pass_a"], query["label_pass_b"])
            self.assertEqual(query["label_pass_a"], query["relevant_ids"])

    def test_manifest_rejects_label_disagreement(self) -> None:
        manifest = copy.deepcopy(image_benchmark.load_manifest())
        manifest["queries"][0]["label_pass_b"] = ["arch_chat"]

        with self.assertRaisesRegex(ValueError, "two-pass label consensus"):
            image_benchmark.validate_manifest(manifest)


class ImageBenchmarkPreparationTests(unittest.TestCase):
    def test_prepare_generates_assets_queries_and_reference_caption_cache(self) -> None:
        def fake_download(asset, target, *, force, timeout_seconds):
            self.assertFalse(force)
            self.assertGreater(timeout_seconds, 0)
            target.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (240, 320), color="#f4f4f4")
            image.save(target)
            return True

        with TemporaryDirectory() as temp_dir, patch.object(
            image_benchmark,
            "_download_asset",
            side_effect=fake_download,
        ):
            runtime_dir = Path(temp_dir)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "captions.json").write_text(
                "{broken cache",
                encoding="utf-8",
            )
            summary = image_benchmark.prepare_benchmark(
                manifest_path=image_benchmark.DEFAULT_MANIFEST_PATH,
                runtime_dir=runtime_dir,
                force_download=False,
                refresh_captions=False,
                seed_reference_captions=True,
                timeout_seconds=5.0,
            )

            self.assertEqual(summary["asset_count"], 30)
            self.assertEqual(summary["downloaded_assets"], 22)
            self.assertEqual(summary["generated_assets"], 8)
            self.assertEqual(summary["perturbed_query_images"], 6)
            self.assertEqual(summary["caption_sources"], {"reference": 30})
            self.assertEqual(summary["query_caption_sources"], {"reference": 6})
            self.assertFalse(summary["vision_caption_cache"])
            self.assertEqual(
                len(list((runtime_dir / "ingest_docs").iterdir())),
                30,
            )
            self.assertEqual(
                len(list((runtime_dir / "query_images").iterdir())),
                6,
            )

            cache = image_benchmark._read_json(runtime_dir / "captions.json")
            self.assertEqual(len(cache["asset_captions"]), 30)
            self.assertEqual(len(cache["query_captions"]), 6)
            self.assertEqual(
                cache["asset_captions"]["arch_chat"]["source"],
                "reference",
            )

    def test_reference_captions_require_explicit_smoke_mode(self) -> None:
        cache = {
            "asset_captions": {
                "arch_chat": {
                    "caption": "Reference caption",
                    "source": "reference",
                }
            },
            "query_captions": {},
        }

        with self.assertRaisesRegex(RuntimeError, "requires cached Vision captions"):
            image_benchmark._require_vision_caption_cache(
                cache,
                allow_reference_captions=False,
            )
        self.assertFalse(
            image_benchmark._require_vision_caption_cache(
                cache,
                allow_reference_captions=True,
            )
        )

    def test_vision_caption_cache_rejects_changed_caption_route(self) -> None:
        with TemporaryDirectory() as temp_dir:
            route_path = Path(temp_dir) / "multimodal.py"
            route_path.write_text("current prompt", encoding="utf-8")
            cache = {
                "caption_generation": {
                    "mode": "vision",
                    "model": "vision-model",
                    "base_url": "https://vision.example/v1",
                    "route_version": image_benchmark.CAPTION_ROUTE_VERSION,
                    "route_sha256": "0" * 64,
                }
            }

            with self.assertRaisesRegex(RuntimeError, "caption route source"):
                image_benchmark._validate_vision_caption_identity(
                    cache,
                    current_model="vision-model",
                    current_base_url="https://vision.example/v1",
                    route_path=route_path,
                )

    def test_vision_caption_cache_rejects_changed_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            route_path = Path(temp_dir) / "multimodal.py"
            route_path.write_text("current prompt", encoding="utf-8")
            cache = {
                "caption_generation": {
                    "mode": "vision",
                    "model": "vision-model",
                    "base_url": "https://old.example/v1",
                    "route_version": image_benchmark.CAPTION_ROUTE_VERSION,
                    "route_sha256": image_benchmark._sha256_file(route_path),
                }
            }

            with self.assertRaisesRegex(RuntimeError, "Vision endpoint"):
                image_benchmark._validate_vision_caption_identity(
                    cache,
                    current_model="vision-model",
                    current_base_url="https://new.example/v1",
                    route_path=route_path,
                )

    def test_official_run_requires_pinned_image_revision(self) -> None:
        settings = SimpleNamespace(IMAGE_EMBEDDING_REVISION="")

        with self.assertRaisesRegex(RuntimeError, "immutable"):
            image_benchmark._require_pinned_image_revision(
                settings,
                official_run=True,
            )
        image_benchmark._require_pinned_image_revision(
            settings,
            official_run=False,
        )

    def test_official_run_rejects_mutable_image_revision(self) -> None:
        settings = SimpleNamespace(IMAGE_EMBEDDING_REVISION="main")

        with self.assertRaisesRegex(RuntimeError, "40-character"):
            image_benchmark._require_pinned_image_revision(
                settings,
                official_run=True,
            )

    def test_index_summary_rejects_changed_text_embedding_identity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            chroma_path = runtime_dir / "chroma"
            image_benchmark._write_json(
                runtime_dir / "captions.json",
                {"caption": "cached"},
            )
            summary = {
                "manifest_sha256": image_benchmark._manifest_digest(
                    image_benchmark.DEFAULT_MANIFEST_PATH
                ),
                "caption_cache_sha256": image_benchmark._sha256_file(
                    runtime_dir / "captions.json"
                ),
                "text_embedding_identity": {
                    "provider": "local",
                    "model": "old-model",
                    "base_url": None,
                },
                "image_embedding_model": "clip-model",
                "chroma_path": str(chroma_path),
            }

            with self.assertRaisesRegex(RuntimeError, "text embedding identity"):
                image_benchmark._validate_index_summary(
                    summary,
                    manifest_path=image_benchmark.DEFAULT_MANIFEST_PATH,
                    runtime_dir=runtime_dir,
                    chroma_path=chroma_path,
                    current_text_embedding_identity={
                        "provider": "local",
                        "model": "new-model",
                        "base_url": None,
                    },
                    current_image_embedding_identity="clip-model",
                )

    def test_chroma_rebuild_is_restricted_to_runtime_directory(self) -> None:
        with (
            TemporaryDirectory() as runtime_temp,
            TemporaryDirectory() as other_temp,
            self.assertRaisesRegex(ValueError, "inside its runtime directory"),
        ):
            image_benchmark._assert_runtime_chroma_path(
                Path(runtime_temp).resolve(),
                Path(other_temp).resolve(),
            )


class ImageBenchmarkMetricTests(unittest.TestCase):
    def test_rank_metrics_support_multiple_relevant_assets(self) -> None:
        metrics = image_benchmark.compute_rank_metrics(
            ["doc_a", "doc_b"],
            ["doc_x", "doc_a", "doc_b"],
        )

        self.assertEqual(metrics["recall_at_1"], 0.0)
        self.assertEqual(metrics["recall_at_3"], 1.0)
        self.assertEqual(metrics["mrr"], 0.5)

    def test_keep_gate_requires_mrr_win_and_two_non_degrading_categories(self) -> None:
        aggregate = {
            "caption_only": {
                "overall": {"mrr": 0.50},
                "by_category": {
                    "architecture": {"recall_at_3": 0.70},
                    "ui": {"recall_at_3": 0.60},
                    "error": {"recall_at_3": 0.80},
                },
            },
            "clip_only": {
                "overall": {"mrr": 0.55},
                "by_category": {
                    "architecture": {"recall_at_3": 0.75},
                    "ui": {"recall_at_3": 0.65},
                    "error": {"recall_at_3": 0.75},
                },
            },
            "fusion": {
                "overall": {"mrr": 0.60},
                "by_category": {
                    "architecture": {"recall_at_3": 0.80},
                    "ui": {"recall_at_3": 0.65},
                    "error": {"recall_at_3": 0.70},
                },
            },
        }

        gate = image_benchmark.evaluate_keep_gate(aggregate)

        self.assertTrue(gate["passed"])
        self.assertEqual(
            gate["non_degrading_recall_at_3_categories"],
            ["architecture", "ui"],
        )


if __name__ == "__main__":
    unittest.main()

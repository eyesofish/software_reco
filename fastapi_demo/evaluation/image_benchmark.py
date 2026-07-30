"""Prepare and evaluate the synthetic multimodal image-retrieval benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import textwrap
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("image_benchmark_manifest.json")
DEFAULT_RUNTIME_DIR = Path(__file__).with_name("image_benchmark_runtime")
DEFAULT_TOP_K = 10
BENCHMARK_CATEGORIES = ("architecture", "ui", "error")
BENCHMARK_MODALITIES = ("text", "image")
BENCHMARK_METHODS = ("caption_only", "clip_only", "fusion")
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
CAPTION_ROUTE_VERSION = "describe_local_image-v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _unique_strings(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in list(value or []):
        text = str(item or "").strip()
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def load_manifest(path: str | Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = _resolve_path(path)
    manifest = _read_json(manifest_path)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("Image benchmark manifest schema_version must be 1")

    licenses = list(manifest.get("licenses") or [])
    license_ids = {
        str(item.get("id", "") or "").strip()
        for item in licenses
        if isinstance(item, dict)
    }
    if not license_ids or "" in license_ids:
        raise ValueError("Every benchmark license must have a non-empty id")

    assets = list(manifest.get("assets") or [])
    if not assets:
        raise ValueError("Image benchmark manifest has no assets")

    asset_ids: set[str] = set()
    category_counts: Counter[str] = Counter()
    category_by_asset: dict[str, str] = {}
    for raw_asset in assets:
        if not isinstance(raw_asset, dict):
            raise ValueError("Every benchmark asset must be an object")
        asset_id = str(raw_asset.get("id", "") or "").strip()
        if not asset_id or asset_id in asset_ids:
            raise ValueError(f"Invalid or duplicate benchmark asset id: {asset_id!r}")
        asset_ids.add(asset_id)

        category = str(raw_asset.get("category", "") or "").strip()
        if category not in BENCHMARK_CATEGORIES:
            raise ValueError(f"Asset {asset_id} has unsupported category: {category}")
        category_counts[category] += 1
        category_by_asset[asset_id] = category

        filename = str(raw_asset.get("filename", "") or "").strip()
        if not filename or Path(filename).name != filename:
            raise ValueError(f"Asset {asset_id} must use a basename-only filename")
        if not str(raw_asset.get("reference_caption", "") or "").strip():
            raise ValueError(f"Asset {asset_id} is missing reference_caption")

        source = raw_asset.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"Asset {asset_id} is missing source metadata")
        license_id = str(source.get("license_id", "") or "").strip()
        if license_id not in license_ids:
            raise ValueError(f"Asset {asset_id} references unknown license {license_id!r}")
        source_kind = str(source.get("kind", "") or "").strip()
        if source_kind == "download":
            url = str(source.get("url", "") or "").strip()
            digest = str(source.get("sha256", "") or "").strip().lower()
            if not url.startswith("https://"):
                raise ValueError(f"Asset {asset_id} download URL must use HTTPS")
            if not _SHA256_PATTERN.fullmatch(digest):
                raise ValueError(f"Asset {asset_id} has an invalid SHA-256 digest")
        elif source_kind == "generated":
            lines = _unique_strings(source.get("lines"))
            if not str(source.get("title", "") or "").strip() or not lines:
                raise ValueError(f"Generated asset {asset_id} requires title and lines")
        else:
            raise ValueError(f"Asset {asset_id} has unsupported source kind: {source_kind}")

    expected_assets = manifest.get("expected_asset_counts")
    if not isinstance(expected_assets, dict):
        raise ValueError("Manifest must declare expected_asset_counts")
    for category in BENCHMARK_CATEGORIES:
        expected = int(expected_assets.get(category, -1))
        if category_counts[category] != expected:
            raise ValueError(
                f"Expected {expected} {category} assets, found {category_counts[category]}"
            )

    queries = list(manifest.get("queries") or [])
    if not queries:
        raise ValueError("Image benchmark manifest has no queries")

    query_ids: set[str] = set()
    modality_counts: Counter[str] = Counter()
    for raw_query in queries:
        if not isinstance(raw_query, dict):
            raise ValueError("Every benchmark query must be an object")
        query_id = str(raw_query.get("id", "") or "").strip()
        if not query_id or query_id in query_ids:
            raise ValueError(f"Invalid or duplicate benchmark query id: {query_id!r}")
        query_ids.add(query_id)

        category = str(raw_query.get("category", "") or "").strip()
        modality = str(raw_query.get("modality", "") or "").strip()
        if category not in BENCHMARK_CATEGORIES:
            raise ValueError(f"Query {query_id} has unsupported category: {category}")
        if modality not in BENCHMARK_MODALITIES:
            raise ValueError(f"Query {query_id} has unsupported modality: {modality}")
        modality_counts[modality] += 1
        if not str(raw_query.get("text", "") or "").strip():
            raise ValueError(f"Query {query_id} is missing text")

        pass_a = sorted(_unique_strings(raw_query.get("label_pass_a")))
        pass_b = sorted(_unique_strings(raw_query.get("label_pass_b")))
        relevant = sorted(_unique_strings(raw_query.get("relevant_ids")))
        if not relevant or pass_a != pass_b or pass_a != relevant:
            raise ValueError(f"Query {query_id} does not have two-pass label consensus")
        for asset_id in relevant:
            if asset_id not in asset_ids:
                raise ValueError(f"Query {query_id} references unknown asset {asset_id}")
            if category_by_asset[asset_id] != category:
                raise ValueError(
                    f"Query {query_id} category does not match relevant asset {asset_id}"
                )

        if modality == "image":
            source_asset_id = str(raw_query.get("query_asset_id", "") or "").strip()
            if source_asset_id not in asset_ids:
                raise ValueError(f"Image query {query_id} has an unknown query_asset_id")
            if category_by_asset[source_asset_id] != category:
                raise ValueError(
                    f"Image query {query_id} category does not match its source asset"
                )

    expected_queries = manifest.get("expected_query_counts")
    if not isinstance(expected_queries, dict):
        raise ValueError("Manifest must declare expected_query_counts")
    for modality in BENCHMARK_MODALITIES:
        expected = int(expected_queries.get(modality, -1))
        if modality_counts[modality] != expected:
            raise ValueError(
                f"Expected {expected} {modality} queries, found {modality_counts[modality]}"
            )


def _asset_path(runtime_dir: Path, asset: dict[str, Any]) -> Path:
    return runtime_dir / "ingest_docs" / str(asset["filename"])


def _query_image_path(runtime_dir: Path, query: dict[str, Any]) -> Path:
    return runtime_dir / "query_images" / f"{query['id']}.png"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_asset(
    asset: dict[str, Any],
    target: Path,
    *,
    force: bool,
    timeout_seconds: float,
) -> bool:
    source = dict(asset["source"])
    expected_digest = str(source["sha256"])
    if target.is_file() and not force and _sha256_file(target) == expected_digest:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.part")
    request = urllib.request.Request(
        str(source["url"]),
        headers={"User-Agent": "software-reco-image-benchmark/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            downloaded = 0
            digest = hashlib.sha256()
            with (
                urllib.request.urlopen(request, timeout=timeout_seconds) as response,
                temporary.open("wb") as handle,
            ):
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"Asset {asset['id']} exceeds the {MAX_DOWNLOAD_BYTES}-byte limit"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            actual_digest = digest.hexdigest()
            if actual_digest != expected_digest:
                raise ValueError(
                    f"SHA-256 mismatch for {asset['id']}: "
                    f"expected {expected_digest}, got {actual_digest}"
                )
            temporary.replace(target)
            return True
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(float(attempt))
    raise RuntimeError(f"Failed to download benchmark asset {asset['id']}: {last_error}")


def _load_monospace_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _is_error_line(line: str) -> bool:
    lowered = line.lower()
    markers = (
        "error",
        "fatal",
        "failed",
        "failure",
        "traceback",
        "assertion",
        "module not found",
        "modulenotfound",
        "crashloopbackoff",
        "conflict",
        "connection refused",
        "ts2322",
    )
    return any(marker in lowered for marker in markers)


def _generate_error_screenshot(asset: dict[str, Any], target: Path) -> None:
    source = dict(asset["source"])
    image = Image.new("RGB", (1280, 720), "#0d1117")
    draw = ImageDraw.Draw(image)
    title_font = _load_monospace_font(22)
    body_font = _load_monospace_font(24)
    small_font = _load_monospace_font(16)

    draw.rectangle((0, 0, 1280, 52), fill="#161b22")
    for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        left = 20 + index * 28
        draw.ellipse((left, 17, left + 14, 31), fill=color)
    draw.text((118, 15), str(source["title"]), font=title_font, fill="#c9d1d9")

    y = 82
    for raw_line in list(source["lines"]):
        line = str(raw_line)
        wrapped_lines = (
            textwrap.wrap(
                line,
                width=94,
                replace_whitespace=False,
                drop_whitespace=False,
            )
            if line
            else [""]
        )
        for wrapped in wrapped_lines:
            color = "#ff7b72" if _is_error_line(wrapped) else "#c9d1d9"
            if wrapped.startswith("$"):
                color = "#7ee787"
            draw.text((38, y), wrapped, font=body_font, fill=color)
            y += 36
        y += 3
    draw.text(
        (38, 685),
        f"synthetic benchmark asset: {asset['id']}",
        font=small_font,
        fill="#8b949e",
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)


def _perturb_image(source: Path, target: Path) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        crop_x = max(1, round(width * 0.035))
        crop_y = max(1, round(height * 0.035))
        cropped = image.crop(
            (
                crop_x,
                crop_y,
                max(crop_x + 1, width - max(1, crop_x // 2)),
                max(crop_y + 1, height - crop_y),
            )
        )
        resized = cropped.resize((width, height), Image.Resampling.LANCZOS)
        adjusted = ImageEnhance.Contrast(resized).enhance(1.04)
        adjusted = ImageEnhance.Brightness(adjusted).enhance(0.98)
        target.parent.mkdir(parents=True, exist_ok=True)
        adjusted.save(target, format="PNG", optimize=True)


def _seed_caption_cache(
    manifest: dict[str, Any],
    manifest_path: Path,
    runtime_dir: Path,
) -> dict[str, Any]:
    cache_path = runtime_dir / "captions.json"
    digest = _manifest_digest(manifest_path)
    asset_captions: dict[str, dict[str, Any]] = {}
    for asset in list(manifest["assets"]):
        asset_id = str(asset["id"])
        asset_captions[asset_id] = {
            "caption": str(asset["reference_caption"]),
            "source": "reference",
            "model": None,
        }

    query_captions: dict[str, dict[str, Any]] = {}
    for query in list(manifest["queries"]):
        if query["modality"] != "image":
            continue
        query_id = str(query["id"])
        query_captions[query_id] = {
            "caption": str(query["text"]),
            "source": "reference",
            "model": None,
        }

    payload = {
        "schema_version": 1,
        "manifest_name": manifest["name"],
        "manifest_sha256": digest,
        "updated_at": _utc_now(),
        "caption_generation": {
            "mode": "reference",
            "model": None,
            "base_url": None,
            "route_version": CAPTION_ROUTE_VERSION,
            "route_sha256": None,
        },
        "asset_captions": asset_captions,
        "query_captions": query_captions,
    }
    _write_json(cache_path, payload)
    return payload


def _refresh_caption_cache(
    manifest: dict[str, Any],
    manifest_path: Path,
    runtime_dir: Path,
) -> dict[str, Any]:
    root_dir = Path(__file__).resolve().parents[1]
    load_dotenv(root_dir / ".env")

    from software_recommend_system import multimodal
    from software_recommend_system.config import settings

    asset_captions: dict[str, dict[str, Any]] = {}
    for asset in list(manifest["assets"]):
        description = multimodal.describe_local_image(_asset_path(runtime_dir, asset))
        asset_captions[str(asset["id"])] = {
            "caption": str(description["description"]),
            "source": "vision",
            "model": str(settings.VISION_MODEL),
        }

    query_captions: dict[str, dict[str, Any]] = {}
    for query in list(manifest["queries"]):
        if query["modality"] != "image":
            continue
        description = multimodal.describe_local_image(
            _query_image_path(runtime_dir, query)
        )
        query_captions[str(query["id"])] = {
            "caption": str(description["description"]),
            "source": "vision",
            "model": str(settings.VISION_MODEL),
        }

    payload = {
        "schema_version": 1,
        "manifest_name": manifest["name"],
        "manifest_sha256": _manifest_digest(manifest_path),
        "updated_at": _utc_now(),
        "caption_generation": {
            "mode": "vision",
            "model": str(settings.VISION_MODEL),
            "base_url": _resolved_vision_base_url(settings),
            "route_version": CAPTION_ROUTE_VERSION,
            "route_sha256": _sha256_file(Path(multimodal.__file__).resolve()),
        },
        "asset_captions": asset_captions,
        "query_captions": query_captions,
    }
    _write_json(runtime_dir / "captions.json", payload)
    return payload


def prepare_benchmark(
    *,
    manifest_path: Path,
    runtime_dir: Path,
    force_download: bool,
    refresh_captions: bool,
    seed_reference_captions: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    if refresh_captions and seed_reference_captions:
        raise ValueError(
            "refresh_captions and seed_reference_captions cannot both be enabled"
        )
    manifest = load_manifest(manifest_path)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    reused = 0
    generated = 0
    for asset in list(manifest["assets"]):
        target = _asset_path(runtime_dir, asset)
        source_kind = str(asset["source"]["kind"])
        if source_kind == "download":
            changed = _download_asset(
                asset,
                target,
                force=force_download,
                timeout_seconds=timeout_seconds,
            )
            downloaded += int(changed)
            reused += int(not changed)
        else:
            _generate_error_screenshot(asset, target)
            generated += 1

    asset_by_id = {str(item["id"]): item for item in list(manifest["assets"])}
    image_queries = [
        query
        for query in list(manifest["queries"])
        if str(query["modality"]) == "image"
    ]
    for query in image_queries:
        source_asset = asset_by_id[str(query["query_asset_id"])]
        _perturb_image(
            _asset_path(runtime_dir, source_asset),
            _query_image_path(runtime_dir, query),
        )

    cache_path = runtime_dir / "captions.json"
    existing_cache = (
        _load_caption_cache(manifest, manifest_path, runtime_dir)
        if cache_path.is_file()
        and not refresh_captions
        and not seed_reference_captions
        else None
    )
    if seed_reference_captions:
        caption_cache = _seed_caption_cache(manifest, manifest_path, runtime_dir)
    elif refresh_captions or not _uses_vision_caption_cache(existing_cache):
        caption_cache = _refresh_caption_cache(manifest, manifest_path, runtime_dir)
    else:
        caption_cache = existing_cache
    if caption_cache is None:
        raise RuntimeError("Caption cache preparation did not produce a cache")

    summary = {
        "manifest": str(manifest_path),
        "manifest_sha256": _manifest_digest(manifest_path),
        "runtime_dir": str(runtime_dir),
        "asset_count": len(manifest["assets"]),
        "query_count": len(manifest["queries"]),
        "downloaded_assets": downloaded,
        "reused_downloads": reused,
        "generated_assets": generated,
        "perturbed_query_images": len(image_queries),
        "caption_sources": _caption_source_counts(
            dict(caption_cache["asset_captions"])
        ),
        "query_caption_sources": _caption_source_counts(
            dict(caption_cache["query_captions"])
        ),
        "vision_caption_cache": _uses_vision_caption_cache(caption_cache),
        "prepared_at": _utc_now(),
    }
    _write_json(runtime_dir / "preparation-summary.json", summary)
    return summary


def _load_caption_cache(
    manifest: dict[str, Any],
    manifest_path: Path,
    runtime_dir: Path,
) -> dict[str, Any]:
    cache_path = runtime_dir / "captions.json"
    cache = _read_json(cache_path)
    if cache.get("manifest_sha256") != _manifest_digest(manifest_path):
        raise ValueError(
            f"Caption cache is stale for {manifest_path}; rerun the prepare command"
        )
    asset_captions = dict(cache.get("asset_captions") or {})
    query_captions = dict(cache.get("query_captions") or {})
    missing_assets = [
        str(asset["id"])
        for asset in list(manifest["assets"])
        if not str(dict(asset_captions.get(str(asset["id"])) or {}).get("caption", "")).strip()
    ]
    missing_queries = [
        str(query["id"])
        for query in list(manifest["queries"])
        if query["modality"] == "image"
        and not str(dict(query_captions.get(str(query["id"])) or {}).get("caption", "")).strip()
    ]
    if missing_assets or missing_queries:
        raise ValueError(
            "Caption cache is incomplete: "
            f"assets={missing_assets}, image_queries={missing_queries}"
        )
    return cache


def _caption_source_counts(entries: dict[str, Any]) -> dict[str, int]:
    return dict(
        Counter(
            str(dict(entry).get("source", "unknown"))
            for entry in entries.values()
            if isinstance(entry, dict)
        )
    )


def _uses_vision_caption_cache(cache: dict[str, Any] | None) -> bool:
    if not cache:
        return False
    asset_entries = dict(cache.get("asset_captions") or {})
    query_entries = dict(cache.get("query_captions") or {})
    all_entries = [*asset_entries.values(), *query_entries.values()]
    return bool(all_entries) and all(
        isinstance(entry, dict) and entry.get("source") == "vision"
        for entry in all_entries
    )


def _require_vision_caption_cache(
    cache: dict[str, Any],
    *,
    allow_reference_captions: bool,
) -> bool:
    uses_vision = _uses_vision_caption_cache(cache)
    if not uses_vision and not allow_reference_captions:
        raise RuntimeError(
            "The benchmark requires cached Vision captions. Run `prepare` with a "
            "configured Vision endpoint, or pass --allow-reference-captions for "
            "a non-decision smoke run."
        )
    return uses_vision


def _validate_vision_caption_identity(
    cache: dict[str, Any],
    *,
    current_model: str,
    current_base_url: str | None,
    route_path: Path,
) -> None:
    generation = dict(cache.get("caption_generation") or {})
    expected_route_sha256 = _sha256_file(route_path)
    mismatches: list[str] = []
    if generation.get("mode") != "vision":
        mismatches.append("caption mode")
    if str(generation.get("model", "") or "") != current_model:
        mismatches.append("Vision model")
    if generation.get("base_url") != current_base_url:
        mismatches.append("Vision endpoint")
    if generation.get("route_version") != CAPTION_ROUTE_VERSION:
        mismatches.append("caption route version")
    if generation.get("route_sha256") != expected_route_sha256:
        mismatches.append("caption route source")
    if mismatches:
        raise RuntimeError(
            "Cached Vision captions are stale for the current "
            f"{', '.join(mismatches)}; rerun `prepare --refresh-captions`."
        )


def _resolved_vision_base_url(settings: Any) -> str | None:
    return (
        str(settings.VISION_BASE_URL or "").strip()
        or str(settings.BASE_URL or "").strip()
        or str(settings.OPENAI_BASE_URL or "").strip()
        or None
    )


def _require_pinned_image_revision(settings: Any, *, official_run: bool) -> None:
    revision = str(settings.IMAGE_EMBEDDING_REVISION or "").strip().lower()
    if official_run and not _GIT_COMMIT_PATTERN.fullmatch(revision):
        raise RuntimeError(
            "Official image benchmark runs require an immutable "
            "40-character IMAGE_EMBEDDING_REVISION commit."
        )


def _text_embedding_identity(settings: Any, text_embedder: Any) -> dict[str, Any]:
    provider = text_embedder._normalize_provider(settings.EMBEDDING_PROVIDER)
    return {
        "provider": provider,
        "model": text_embedder._resolve_embedding_model(provider),
        "base_url": (
            text_embedder._resolve_embedding_base_url(provider)
            if provider != "local"
            else None
        ),
    }


def _validate_index_summary(
    index_summary: dict[str, Any],
    *,
    manifest_path: Path,
    runtime_dir: Path,
    chroma_path: Path,
    current_text_embedding_identity: dict[str, Any],
    current_image_embedding_identity: str,
) -> None:
    mismatches: list[str] = []
    if index_summary.get("manifest_sha256") != _manifest_digest(manifest_path):
        mismatches.append("manifest")
    if index_summary.get("caption_cache_sha256") != _sha256_file(
        runtime_dir / "captions.json"
    ):
        mismatches.append("caption cache")
    if index_summary.get("text_embedding_identity") != current_text_embedding_identity:
        mismatches.append("text embedding identity")
    if index_summary.get("image_embedding_model") != current_image_embedding_identity:
        mismatches.append("image embedding identity")
    if _resolve_path(str(index_summary.get("chroma_path", ""))) != chroma_path:
        mismatches.append("Chroma path")
    if mismatches:
        raise RuntimeError(
            "Image benchmark index is stale for the current "
            f"{', '.join(mismatches)}; rerun the build-index command."
        )


def _assert_runtime_chroma_path(runtime_dir: Path, chroma_path: Path) -> None:
    try:
        chroma_path.relative_to(runtime_dir)
    except ValueError as exc:
        raise ValueError(
            "The image benchmark only rebuilds Chroma paths inside its runtime directory"
        ) from exc


def _in_batches(items: Sequence[Any], batch_size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def _embed_batches(
    items: Sequence[Any],
    *,
    batch_size: int,
    embed: Callable[[list[Any]], list[list[float]]],
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for batch in _in_batches(items, batch_size):
        batch_vectors = embed(list(batch))
        if len(batch_vectors) != len(batch):
            raise RuntimeError(
                f"Embedding batch returned {len(batch_vectors)} vectors for {len(batch)} inputs"
            )
        vectors.extend(batch_vectors)
    return vectors


def build_index(
    *,
    manifest_path: Path,
    runtime_dir: Path,
    chroma_path: Path,
    batch_size: int,
    allow_reference_captions: bool,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    manifest = load_manifest(manifest_path)
    caption_cache = _load_caption_cache(manifest, manifest_path, runtime_dir)
    uses_vision_captions = _require_vision_caption_cache(
        caption_cache,
        allow_reference_captions=allow_reference_captions,
    )
    _assert_runtime_chroma_path(runtime_dir, chroma_path)

    root_dir = Path(__file__).resolve().parents[1]
    load_dotenv(root_dir / ".env")
    os.environ["CHROMA_DB_PATH"] = str(chroma_path)
    os.environ["ENABLE_PARENT_CHILD_CHUNKING"] = "false"
    os.environ["RECALL_ENABLE_IMAGE_VECTOR"] = "true"
    os.environ["EMBEDDING_ENABLE_FALLBACK"] = "false"

    import chromadb

    from software_recommend_system import multimodal
    from software_recommend_system.config import settings
    from software_recommend_system.image_embedder import (
        embed_image_files,
        image_embedding_identity,
    )
    from software_recommend_system.ingestion import embedder as text_embedder
    from software_recommend_system.ingestion.indexer import (
        get_chroma_collection,
        get_image_collection,
        index_embeddings,
        index_image_embeddings,
        resolve_image_collection_name,
    )

    if uses_vision_captions:
        _validate_vision_caption_identity(
            caption_cache,
            current_model=str(settings.VISION_MODEL),
            current_base_url=_resolved_vision_base_url(settings),
            route_path=Path(multimodal.__file__).resolve(),
        )
    _require_pinned_image_revision(
        settings,
        official_run=uses_vision_captions,
    )
    text_embedding_identity = _text_embedding_identity(settings, text_embedder)
    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    collections_before = [
        getattr(item, "name", str(item))
        for item in client.list_collections()
    ]
    for collection_name in collections_before:
        client.delete_collection(collection_name)

    assets = list(manifest["assets"])
    asset_captions = dict(caption_cache["asset_captions"])
    ids = [str(asset["id"]) for asset in assets]
    captions = [
        str(dict(asset_captions[str(asset["id"])])["caption"])
        for asset in assets
    ]
    paths = [_asset_path(runtime_dir, asset) for asset in assets]
    missing_files = [str(path) for path in paths if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(
            f"Prepared benchmark assets are missing: {missing_files}"
        )

    metadatas = [
        {
            "source": "image_benchmark",
            "doc_id": str(asset["id"]),
            "source_doc_id": str(asset["id"]),
            "filename": str(asset["filename"]),
            "path": str(asset["filename"]),
            "asset_path": str(asset["filename"]),
            "media_type": (
                "image/jpeg"
                if str(asset["filename"]).lower().endswith((".jpg", ".jpeg"))
                else "image/png"
            ),
            "modality": "image",
            "benchmark_category": str(asset["category"]),
        }
        for asset in assets
    ]

    text_vectors = _embed_batches(
        captions,
        batch_size=batch_size,
        embed=text_embedder.embed_texts,
    )
    image_vectors = _embed_batches(
        paths,
        batch_size=batch_size,
        embed=embed_image_files,
    )
    text_count = index_embeddings(
        chunk_ids=ids,
        chunk_texts=captions,
        metadatas=metadatas,
        embeddings=text_vectors,
    )
    image_count = index_image_embeddings(
        image_ids=ids,
        captions=captions,
        metadatas=metadatas,
        embeddings=image_vectors,
    )

    text_collection = get_chroma_collection()
    image_collection = get_image_collection()
    summary = {
        "manifest": str(manifest_path),
        "manifest_sha256": _manifest_digest(manifest_path),
        "runtime_dir": str(runtime_dir),
        "chroma_path": str(chroma_path),
        "caption_cache_sha256": _sha256_file(runtime_dir / "captions.json"),
        "asset_count": len(assets),
        "text_vectors": text_count,
        "image_vectors": image_count,
        "text_collection": "software_recommendations",
        "text_collection_count": int(text_collection.count()),
        "image_collection": resolve_image_collection_name(),
        "image_collection_count": int(image_collection.count()),
        "text_embedding_identity": text_embedding_identity,
        "text_embedding_provider": text_embedding_identity["provider"],
        "text_embedding_model": text_embedding_identity["model"],
        "image_embedding_model": image_embedding_identity(),
        "caption_sources": _caption_source_counts(asset_captions),
        "query_caption_sources": _caption_source_counts(
            dict(caption_cache["query_captions"])
        ),
        "vision_caption_cache": uses_vision_captions,
        "caption_generation": dict(caption_cache.get("caption_generation") or {}),
        "built_at": _utc_now(),
    }
    _write_json(runtime_dir / "index-summary.json", summary)
    return summary


def compute_rank_metrics(
    relevant_ids: Sequence[str],
    retrieved_ids: Sequence[str],
) -> dict[str, float]:
    relevant = set(_unique_strings(relevant_ids))
    if not relevant:
        raise ValueError("relevant_ids must be non-empty")
    retrieved = _unique_strings(retrieved_ids)

    def recall_at(cutoff: int) -> float:
        hits = sum(1 for item in retrieved[:cutoff] if item in relevant)
        return hits / len(relevant)

    reciprocal_rank = 0.0
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "recall_at_1": recall_at(1),
        "recall_at_3": recall_at(3),
        "mrr": reciprocal_rank,
    }


def _mean(values: Iterable[float]) -> float:
    numbers = [float(value) for value in values]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _doc_id(document: Any) -> str:
    metadata = getattr(document, "metadata", {}) or {}
    if hasattr(metadata, "model_dump"):
        metadata = metadata.model_dump(exclude_none=True)
    if not isinstance(metadata, dict):
        return ""
    return str(
        metadata.get("doc_id")
        or metadata.get("source_doc_id")
        or ""
    ).strip()


def _ranked_ids(documents: Sequence[Any], top_k: int) -> list[str]:
    return _unique_strings(
        _doc_id(document)
        for document in list(documents)[:top_k]
    )


def _aggregate_results(per_query: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for method in BENCHMARK_METHODS:
        rows = [row for row in per_query if row["method"] == method]
        aggregate[method] = {
            "overall": {
                metric: _mean(row[metric] for row in rows)
                for metric in ("recall_at_1", "recall_at_3", "mrr")
            },
            "by_category": {
                category: {
                    metric: _mean(
                        row[metric]
                        for row in rows
                        if row["category"] == category
                    )
                    for metric in ("recall_at_1", "recall_at_3", "mrr")
                }
                for category in BENCHMARK_CATEGORIES
            },
            "by_modality": {
                modality: {
                    metric: _mean(
                        row[metric]
                        for row in rows
                        if row["modality"] == modality
                    )
                    for metric in ("recall_at_1", "recall_at_3", "mrr")
                }
                for modality in BENCHMARK_MODALITIES
            },
        }
    return aggregate


def evaluate_keep_gate(aggregate: dict[str, Any]) -> dict[str, Any]:
    caption_mrr = float(aggregate["caption_only"]["overall"]["mrr"])
    clip_mrr = float(aggregate["clip_only"]["overall"]["mrr"])
    fusion_mrr = float(aggregate["fusion"]["overall"]["mrr"])
    epsilon = 1e-12
    mrr_wins = (
        fusion_mrr > caption_mrr + epsilon
        and fusion_mrr > clip_mrr + epsilon
    )

    non_degrading_categories: list[str] = []
    for category in BENCHMARK_CATEGORIES:
        fusion_recall = float(
            aggregate["fusion"]["by_category"][category]["recall_at_3"]
        )
        stronger_baseline = max(
            float(
                aggregate["caption_only"]["by_category"][category]["recall_at_3"]
            ),
            float(
                aggregate["clip_only"]["by_category"][category]["recall_at_3"]
            ),
        )
        if fusion_recall + epsilon >= stronger_baseline:
            non_degrading_categories.append(category)

    return {
        "passed": mrr_wins and len(non_degrading_categories) >= 2,
        "fusion_mrr_beats_both_baselines": mrr_wins,
        "non_degrading_recall_at_3_categories": non_degrading_categories,
        "required_non_degrading_categories": 2,
    }


def _write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "query_id",
                "category",
                "modality",
                "method",
                "query_text",
                "relevant_ids",
                "retrieved_ids",
                "recall_at_1",
                "recall_at_3",
                "mrr",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{
                        key: row[key]
                        for key in (
                            "query_id",
                            "category",
                            "modality",
                            "method",
                            "query_text",
                            "recall_at_1",
                            "recall_at_3",
                            "mrr",
                        )
                    },
                    "relevant_ids": "|".join(row["relevant_ids"]),
                    "retrieved_ids": "|".join(row["retrieved_ids"]),
                }
            )


def run_benchmark(
    *,
    manifest_path: Path,
    runtime_dir: Path,
    chroma_path: Path,
    top_k: int,
    output_json: Path,
    output_csv: Path | None,
    allow_reference_captions: bool,
) -> dict[str, Any]:
    if top_k < 3:
        raise ValueError("top_k must be at least 3")
    manifest = load_manifest(manifest_path)
    caption_cache = _load_caption_cache(manifest, manifest_path, runtime_dir)
    uses_vision_captions = _require_vision_caption_cache(
        caption_cache,
        allow_reference_captions=allow_reference_captions,
    )
    _assert_runtime_chroma_path(runtime_dir, chroma_path)

    root_dir = Path(__file__).resolve().parents[1]
    load_dotenv(root_dir / ".env")
    os.environ["CHROMA_DB_PATH"] = str(chroma_path)
    os.environ["ENABLE_PARENT_CHILD_CHUNKING"] = "false"
    os.environ["RECALL_ENABLE_IMAGE_VECTOR"] = "true"
    os.environ["EMBEDDING_ENABLE_FALLBACK"] = "false"

    import chromadb

    import software_recommend_system.observability as observability
    from software_recommend_system import multimodal
    from software_recommend_system.config import settings
    from software_recommend_system.image_embedder import (
        embed_image_files,
        image_embedding_identity,
    )
    from software_recommend_system.ingestion import embedder as text_embedder
    from software_recommend_system.ingestion.indexer import (
        resolve_image_collection_name,
    )
    from software_recommend_system.retrieval_channels import (
        recall_image_vector,
        recall_vector,
    )
    from software_recommend_system.retriever import (
        _fuse_local_vector_documents,
        _normalize_documents,
    )

    observability._langsmith_traceable = None
    observability._langsmith_wrap_openai = None

    if uses_vision_captions:
        _validate_vision_caption_identity(
            caption_cache,
            current_model=str(settings.VISION_MODEL),
            current_base_url=_resolved_vision_base_url(settings),
            route_path=Path(multimodal.__file__).resolve(),
        )
    _require_pinned_image_revision(
        settings,
        official_run=uses_vision_captions,
    )
    current_text_embedding_identity = _text_embedding_identity(
        settings,
        text_embedder,
    )
    index_summary_path = runtime_dir / "index-summary.json"
    index_summary = _read_json(index_summary_path)
    _validate_index_summary(
        index_summary,
        manifest_path=manifest_path,
        runtime_dir=runtime_dir,
        chroma_path=chroma_path,
        current_text_embedding_identity=current_text_embedding_identity,
        current_image_embedding_identity=image_embedding_identity(),
    )

    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        text_count = int(client.get_collection("software_recommendations").count())
        image_count = int(
            client.get_collection(resolve_image_collection_name()).count()
        )
    except Exception as exc:
        raise RuntimeError(
            "Image benchmark index is missing; run the build-index command first"
        ) from exc
    expected_count = len(manifest["assets"])
    if text_count != expected_count or image_count != expected_count:
        raise RuntimeError(
            "Image benchmark index count mismatch: "
            f"expected={expected_count}, text={text_count}, image={image_count}"
        )

    image_queries = [
        query
        for query in list(manifest["queries"])
        if query["modality"] == "image"
    ]
    query_vectors: dict[str, list[float]] = {}
    if image_queries:
        query_paths = [
            _query_image_path(runtime_dir, query)
            for query in image_queries
        ]
        missing_query_images = [
            str(path)
            for path in query_paths
            if not path.is_file()
        ]
        if missing_query_images:
            raise FileNotFoundError(
                f"Perturbed query images are missing: {missing_query_images}"
            )
        embedded_queries = embed_image_files(query_paths)
        if len(embedded_queries) != len(image_queries):
            raise RuntimeError("Image query embedding count mismatch")
        query_vectors = {
            str(query["id"]): vector
            for query, vector in zip(image_queries, embedded_queries, strict=True)
        }

    query_caption_entries = dict(caption_cache["query_captions"])
    per_query: list[dict[str, Any]] = []
    for query in list(manifest["queries"]):
        query_id = str(query["id"])
        modality = str(query["modality"])
        query_text = (
            str(query["text"])
            if modality == "text"
            else str(dict(query_caption_entries[query_id])["caption"])
        )
        relevant_ids = _unique_strings(query["relevant_ids"])

        caption_docs = _normalize_documents(
            recall_vector(query=query_text, top_k=top_k),
            source_hint="vector",
            channel="vector",
        )
        if modality == "text":
            clip_docs = recall_image_vector(
                query=query_text,
                top_k=top_k,
            )
        else:
            clip_docs = recall_image_vector(
                query="",
                top_k=top_k,
                query_image_embeddings=[query_vectors[query_id]],
            )
        clip_docs = _normalize_documents(
            clip_docs,
            source_hint="image_vector",
            channel="image_vector",
        )
        if not caption_docs:
            raise RuntimeError(f"Caption retrieval returned no results for {query_id}")
        if not clip_docs:
            raise RuntimeError(f"CLIP retrieval returned no results for {query_id}")

        method_documents = {
            "caption_only": caption_docs,
            "clip_only": clip_docs,
            "fusion": _fuse_local_vector_documents(caption_docs, clip_docs),
        }
        for method, documents in method_documents.items():
            retrieved_ids = _ranked_ids(documents, top_k)
            metrics = compute_rank_metrics(relevant_ids, retrieved_ids)
            per_query.append(
                {
                    "query_id": query_id,
                    "category": str(query["category"]),
                    "modality": modality,
                    "method": method,
                    "query_text": query_text,
                    "relevant_ids": relevant_ids,
                    "retrieved_ids": retrieved_ids,
                    **metrics,
                }
            )

    aggregate = _aggregate_results(per_query)
    gate = evaluate_keep_gate(aggregate)
    gate["evaluable"] = uses_vision_captions
    if not uses_vision_captions:
        gate["raw_passed"] = gate["passed"]
        gate["passed"] = False
        gate["reason"] = (
            "Reference captions were used; refresh and cache Vision captions "
            "before making a keep/remove decision."
        )
    payload = {
        "benchmark": manifest["name"],
        "synthetic_labels": True,
        "human_verified": False,
        "manifest": str(manifest_path),
        "manifest_sha256": _manifest_digest(manifest_path),
        "runtime_dir": str(runtime_dir),
        "chroma_path": str(chroma_path),
        "top_k": top_k,
        "asset_count": len(manifest["assets"]),
        "query_count": len(manifest["queries"]),
        "caption_sources": _caption_source_counts(
            dict(caption_cache["asset_captions"])
        ),
        "query_caption_sources": _caption_source_counts(
            dict(caption_cache["query_captions"])
        ),
        "vision_caption_cache": uses_vision_captions,
        "caption_generation": dict(caption_cache.get("caption_generation") or {}),
        "text_embedding_identity": current_text_embedding_identity,
        "image_embedding_model": image_embedding_identity(),
        "aggregate": aggregate,
        "keep_gate": gate,
        "per_query": per_query,
        "evaluated_at": _utc_now(),
    }
    _write_json(output_json, payload)
    if output_csv is not None:
        _write_results_csv(output_csv, per_query)
    return payload


def _print_summary(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare, index, and evaluate the synthetic image benchmark."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Download external assets, generate error screenshots, and seed captions.",
    )
    prepare_parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
    )
    prepare_parser.add_argument(
        "--runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR),
    )
    prepare_parser.add_argument("--force-download", action="store_true")
    prepare_parser.add_argument("--refresh-captions", action="store_true")
    prepare_parser.add_argument(
        "--seed-reference-captions",
        action="store_true",
        help="Skip Vision calls for a smoke run; results cannot satisfy the keep gate.",
    )
    prepare_parser.add_argument("--timeout-seconds", type=float, default=30.0)

    build_parser = subparsers.add_parser(
        "build-index",
        help="Build isolated text-caption and native-image Chroma collections.",
    )
    build_parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
    )
    build_parser.add_argument(
        "--runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR),
    )
    build_parser.add_argument("--chroma-path", default="")
    build_parser.add_argument("--batch-size", type=int, default=8)
    build_parser.add_argument(
        "--allow-reference-captions",
        action="store_true",
        help="Build a smoke-test index from reference captions instead of Vision captions.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Compare caption-only, CLIP-only, and RRF fusion retrieval.",
    )
    run_parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
    )
    run_parser.add_argument(
        "--runtime-dir",
        default=str(DEFAULT_RUNTIME_DIR),
    )
    run_parser.add_argument("--chroma-path", default="")
    run_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    run_parser.add_argument("--output-json", default="")
    run_parser.add_argument("--output-csv", default="")
    run_parser.add_argument(
        "--allow-reference-captions",
        action="store_true",
        help="Run metrics in non-decision smoke mode with reference captions.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = _resolve_path(args.manifest)
    runtime_dir = _resolve_path(args.runtime_dir)
    chroma_path = (
        _resolve_path(args.chroma_path)
        if hasattr(args, "chroma_path") and str(args.chroma_path).strip()
        else runtime_dir / "chroma"
    )

    if args.command == "prepare":
        payload = prepare_benchmark(
            manifest_path=manifest_path,
            runtime_dir=runtime_dir,
            force_download=bool(args.force_download),
            refresh_captions=bool(args.refresh_captions),
            seed_reference_captions=bool(args.seed_reference_captions),
            timeout_seconds=max(2.0, float(args.timeout_seconds)),
        )
    elif args.command == "build-index":
        payload = build_index(
            manifest_path=manifest_path,
            runtime_dir=runtime_dir,
            chroma_path=chroma_path,
            batch_size=int(args.batch_size),
            allow_reference_captions=bool(args.allow_reference_captions),
        )
    else:
        output_json = (
            _resolve_path(args.output_json)
            if str(args.output_json).strip()
            else runtime_dir / "benchmark-results.json"
        )
        output_csv = (
            _resolve_path(args.output_csv)
            if str(args.output_csv).strip()
            else runtime_dir / "benchmark-results.csv"
        )
        payload = run_benchmark(
            manifest_path=manifest_path,
            runtime_dir=runtime_dir,
            chroma_path=chroma_path,
            top_k=int(args.top_k),
            output_json=output_json,
            output_csv=output_csv,
            allow_reference_captions=bool(args.allow_reference_captions),
        )
    _print_summary(payload)


if __name__ == "__main__":
    main()

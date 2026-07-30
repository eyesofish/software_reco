from __future__ import annotations

import logging
import math
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import settings
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id

Image: Any = None
try:
    from PIL import Image as PILImage
except Exception:  # pragma: no cover - optional until image-vector recall is enabled
    pass
else:
    Image = PILImage

SentenceTransformerClass: Any = None
try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
except Exception:  # pragma: no cover - optional until image-vector recall is enabled
    pass
else:
    SentenceTransformerClass = _SentenceTransformer

logger = logging.getLogger(__name__)

_IMAGE_MODELS: dict[tuple[str, str, bool], Any] = {}
_IMAGE_MODEL_LOCK = threading.Lock()
_IMAGE_ENCODE_LOCK = threading.Lock()


class ImageEmbeddingError(RuntimeError):
    """Raised when the native image embedding route cannot encode an input."""


def _model_config() -> tuple[str, str, bool, str]:
    model_name = str(settings.IMAGE_EMBEDDING_MODEL or "").strip()
    if not model_name:
        raise ImageEmbeddingError("IMAGE_EMBEDDING_MODEL is not configured")
    device = str(settings.IMAGE_EMBEDDING_DEVICE or "cpu").strip() or "cpu"
    revision = str(settings.IMAGE_EMBEDDING_REVISION or "").strip()
    return (
        model_name,
        device,
        bool(settings.IMAGE_EMBEDDING_LOCAL_FILES_ONLY),
        revision,
    )


def image_embedding_identity() -> str:
    model_name, _, _, revision = _model_config()
    return f"{model_name}@{revision}" if revision else model_name


def _get_image_model(trace_id: str) -> Any:
    if SentenceTransformerClass is None:
        raise ImageEmbeddingError(
            "sentence-transformers is not installed; native image retrieval is unavailable"
        )

    model_name, device, local_files_only, revision = _model_config()
    cache_key = (image_embedding_identity(), device, local_files_only)
    with _IMAGE_MODEL_LOCK:
        cached = _IMAGE_MODELS.get(cache_key)
        if cached is not None:
            log_event(
                logger,
                logging.INFO,
                "image_embedding.model.cache_hit",
                component="image_embedding",
                trace_id=trace_id,
                model=model_name,
                revision=revision or None,
                device=device,
            )
            return cached

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "image_embedding.model.load.start",
            component="image_embedding",
            trace_id=trace_id,
            model=model_name,
            revision=revision or None,
            device=device,
            local_files_only=local_files_only,
        )
        try:
            model_kwargs: dict[str, Any] = {
                "device": device,
                "local_files_only": local_files_only,
            }
            if revision:
                model_kwargs["revision"] = revision
            model = SentenceTransformerClass(model_name, **model_kwargs)
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "image_embedding.model.load.fail",
                component="image_embedding",
                trace_id=trace_id,
                model=model_name,
                revision=revision or None,
                device=device,
                elapsed_ms=elapsed_ms(started_at),
                **error_fields(exc),
            )
            raise ImageEmbeddingError(
                f"Failed to load image embedding model {model_name}: {exc}"
            ) from exc

        _IMAGE_MODELS[cache_key] = model
        log_event(
            logger,
            logging.INFO,
            "image_embedding.model.load.done",
            component="image_embedding",
            trace_id=trace_id,
            model=model_name,
            revision=revision or None,
            device=device,
            elapsed_ms=elapsed_ms(started_at),
        )
        return model


def _convert_vectors(raw_vectors: Any, expected_count: int) -> list[list[float]]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    rows = list(raw_vectors or [])
    if expected_count == 1 and rows and not isinstance(rows[0], (list, tuple)):
        rows = [rows]
    if len(rows) != expected_count:
        raise ImageEmbeddingError(
            f"Image embedding model returned {len(rows)} vectors for {expected_count} inputs"
        )

    converted: list[list[float]] = []
    expected_dimension = 0
    for row in rows:
        values = [float(value) for value in list(row or [])]
        if not values or not all(math.isfinite(value) for value in values):
            raise ImageEmbeddingError("Image embedding model returned an invalid vector")
        if expected_dimension and len(values) != expected_dimension:
            raise ImageEmbeddingError("Image embedding model returned inconsistent dimensions")
        expected_dimension = len(values)
        converted.append(values)
    return converted


def _encode(inputs: list[Any], *, input_kind: str) -> list[list[float]]:
    if not inputs:
        return []

    trace_id = new_trace_id("image_embedding")
    model_name, device, _, revision = _model_config()
    model = _get_image_model(trace_id)
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "image_embedding.encode.start",
        component="image_embedding",
        trace_id=trace_id,
        model=model_name,
        revision=revision or None,
        device=device,
        input_kind=input_kind,
        input_count=len(inputs),
    )
    try:
        with _IMAGE_ENCODE_LOCK:
            raw_vectors = model.encode(
                inputs,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        vectors = _convert_vectors(raw_vectors, expected_count=len(inputs))
    except ImageEmbeddingError:
        raise
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "image_embedding.encode.fail",
            component="image_embedding",
            trace_id=trace_id,
            model=model_name,
            revision=revision or None,
            device=device,
            input_kind=input_kind,
            input_count=len(inputs),
            elapsed_ms=elapsed_ms(started_at),
            **error_fields(exc),
        )
        raise ImageEmbeddingError(
            f"Image embedding model failed to encode {input_kind} inputs: {exc}"
        ) from exc

    log_event(
        logger,
        logging.INFO,
        "image_embedding.encode.done",
        component="image_embedding",
        trace_id=trace_id,
        model=model_name,
        revision=revision or None,
        device=device,
        input_kind=input_kind,
        input_count=len(inputs),
        dimension=len(vectors[0]) if vectors else 0,
        elapsed_ms=elapsed_ms(started_at),
    )
    return vectors


def embed_image_texts(texts: list[str]) -> list[list[float]]:
    """Encode text into the same shared space used by knowledge images."""
    clean_texts = [str(text).strip() for text in (texts or []) if str(text).strip()]
    return _encode(clean_texts, input_kind="text")


def _open_image(raw: bytes) -> Any:
    if Image is None:
        raise ImageEmbeddingError("Pillow is not installed; native image retrieval is unavailable")
    try:
        with Image.open(BytesIO(raw)) as opened:
            width, height = opened.size
            max_pixels = max(1, int(settings.MULTIMODAL_MAX_IMAGE_PIXELS))
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageEmbeddingError(
                    f"Image dimensions {width}x{height} exceed the "
                    f"{max_pixels}-pixel embedding limit"
                )
            image = opened.convert("RGB")
            image.load()
            return image
    except ImageEmbeddingError:
        raise
    except Exception as exc:
        raise ImageEmbeddingError(f"Failed to decode image for embedding: {exc}") from exc


def embed_image_bytes(images: list[bytes]) -> list[list[float]]:
    """Encode in-memory images without persisting the original bytes."""
    opened_images = [_open_image(bytes(raw)) for raw in (images or [])]
    try:
        return _encode(opened_images, input_kind="image")
    finally:
        for image in opened_images:
            close = getattr(image, "close", None)
            if callable(close):
                close()


def embed_image_files(paths: list[Path]) -> list[list[float]]:
    """Encode image files from the knowledge ingestion directory."""
    payloads: list[bytes] = []
    max_image_bytes = max(1, int(settings.MULTIMODAL_MAX_IMAGE_BYTES))
    for path in paths or []:
        try:
            file_path = Path(path)
            file_size = file_path.stat().st_size
        except OSError as exc:
            raise ImageEmbeddingError(f"Failed to read image {path}: {exc}") from exc
        if file_size > max_image_bytes:
            raise ImageEmbeddingError(
                f"Image {file_path.name} exceeds the {max_image_bytes}-byte embedding limit"
            )
        try:
            payloads.append(file_path.read_bytes())
        except OSError as exc:
            raise ImageEmbeddingError(f"Failed to read image {path}: {exc}") from exc
    return embed_image_bytes(payloads)

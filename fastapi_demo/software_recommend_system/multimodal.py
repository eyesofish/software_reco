from __future__ import annotations

import base64
import binascii
import logging
import time
from pathlib import Path
from typing import Any

import openai

from .config import settings
from .image_embedder import embed_image_bytes
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id
from .observability import wrap_openai

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_MEDIA_TYPES = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
}
IMAGE_MEDIA_TYPE_BY_SUFFIX = {
    suffix: media_type
    for media_type, suffixes in ALLOWED_IMAGE_MEDIA_TYPES.items()
    for suffix in suffixes
}


class MultimodalInputError(ValueError):
    """Raised when an image attachment is malformed or exceeds configured limits."""


class VisionProcessingError(RuntimeError):
    """Raised when the configured vision model cannot describe an image."""


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _detect_image_media_type(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image_data_url(
    data_url: str,
    *,
    expected_media_type: str,
    max_bytes: int,
) -> bytes:
    expected = str(expected_media_type or "").strip().lower()
    if expected not in ALLOWED_IMAGE_MEDIA_TYPES:
        raise MultimodalInputError(f"Unsupported image media type: {expected_media_type}")

    prefix = f"data:{expected};base64,"
    normalized = str(data_url or "").strip()
    if not normalized.startswith(prefix):
        raise MultimodalInputError("Image data_url does not match its declared media_type")

    encoded = normalized[len(prefix):]
    if not encoded:
        raise MultimodalInputError("Image data_url is empty")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MultimodalInputError("Image data_url contains invalid base64") from exc

    if not raw:
        raise MultimodalInputError("Decoded image is empty")
    if len(raw) > max_bytes:
        raise MultimodalInputError(
            f"Image exceeds the {max_bytes}-byte attachment limit"
        )

    detected = _detect_image_media_type(raw)
    if detected != expected:
        raise MultimodalInputError(
            f"Image bytes are {detected or 'unknown'}, not {expected}"
        )
    return raw


def image_bytes_to_data_url(raw: bytes, media_type: str) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _get_vision_client() -> openai.OpenAI:
    api_key = (
        settings.VISION_API_KEY
        or settings.DASHSCOPE_API_KEY
        or settings.OPENAI_API_KEY
        or "LOCAL_DUMMY_KEY"
    )
    base_url = (
        settings.VISION_BASE_URL
        or settings.BASE_URL
        or settings.OPENAI_BASE_URL
        or None
    )
    return wrap_openai(
        openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=max(2.0, float(settings.VISION_TIMEOUT_SECONDS)),
            max_retries=0,
        )
    )


def _extract_response_text(response: Any) -> str:
    choices = _get_value(response, "choices", []) or []
    if not choices:
        return ""
    message = _get_value(choices[0], "message", None)
    content = _get_value(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _get_value(item, "text", "") or _get_value(item, "content", "")
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def caption_image_data_url(
    *,
    name: str,
    media_type: str,
    data_url: str,
    user_query: str = "",
    purpose: str = "query",
) -> str:
    model = str(settings.VISION_MODEL or "").strip()
    if not model:
        raise VisionProcessingError("VISION_MODEL is not configured")

    if purpose == "knowledge":
        instruction = (
            "Describe this software-engineering knowledge image for retrieval. "
            "Capture visible text, UI labels, code, commands, products, versions, "
            "architecture relationships, chart meaning, and error messages. "
            "Be factual and dense; do not guess unreadable details."
        )
    else:
        query_hint = str(user_query or "").strip()
        instruction = (
            "Describe the image facts needed to answer the user's request. "
            "Transcribe relevant visible text and identify UI, code, diagrams, "
            "errors, products, and technical relationships. Do not speculate."
        )
        if query_hint:
            instruction += f"\nUser request: {query_hint}"

    client = _get_vision_client()
    trace_id = new_trace_id("vision")
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "vision.caption.start",
        component="vision",
        trace_id=trace_id,
        model=model,
        purpose=purpose,
        filename=name,
        media_type=media_type,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise visual document analyst for a multimodal RAG system."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                },
            ],
            temperature=0.0,
            max_tokens=max(64, int(settings.VISION_CAPTION_MAX_TOKENS)),
        )
        caption = _extract_response_text(response)
        if not caption:
            raise VisionProcessingError("Vision model returned an empty description")
        log_event(
            logger,
            logging.INFO,
            "vision.caption.done",
            component="vision",
            trace_id=trace_id,
            model=model,
            purpose=purpose,
            filename=name,
            elapsed_ms=elapsed_ms(started_at),
            caption_chars=len(caption),
        )
        return caption
    except VisionProcessingError:
        raise
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "vision.caption.fail",
            component="vision",
            trace_id=trace_id,
            model=model,
            purpose=purpose,
            filename=name,
            elapsed_ms=elapsed_ms(started_at),
            **error_fields(exc),
        )
        raise VisionProcessingError(
            f"Vision model failed to process {name}: {exc}"
        ) from exc


def describe_image_attachments(
    attachments: list[Any],
    *,
    user_query: str,
) -> list[dict[str, str]]:
    if not attachments:
        return []

    max_images = max(1, int(settings.MULTIMODAL_MAX_IMAGES))
    max_image_bytes = max(1, int(settings.MULTIMODAL_MAX_IMAGE_BYTES))
    max_total_bytes = max(max_image_bytes, int(settings.MULTIMODAL_MAX_TOTAL_IMAGE_BYTES))
    if len(attachments) > max_images:
        raise MultimodalInputError(f"At most {max_images} images may be attached")

    descriptions: list[dict[str, str]] = []
    total_bytes = 0
    for index, attachment in enumerate(attachments, start=1):
        name = str(_get_value(attachment, "name", "") or f"image-{index}").strip()
        media_type = str(_get_value(attachment, "media_type", "") or "").strip().lower()
        data_url = str(_get_value(attachment, "data_url", "") or "").strip()
        raw = decode_image_data_url(
            data_url,
            expected_media_type=media_type,
            max_bytes=max_image_bytes,
        )
        total_bytes += len(raw)
        if total_bytes > max_total_bytes:
            raise MultimodalInputError(
                f"Combined image attachments exceed {max_total_bytes} bytes"
            )

        description = caption_image_data_url(
            name=name,
            media_type=media_type,
            data_url=data_url,
            user_query=user_query,
            purpose="query",
        )
        descriptions.append(
            {
                "name": name,
                "media_type": media_type,
                "description": description,
            }
        )
    return descriptions


def embed_image_attachments(attachments: list[Any]) -> list[list[float]]:
    """Encode uploaded images in memory for native image-vector retrieval."""
    if not attachments:
        return []

    max_images = max(1, int(settings.MULTIMODAL_MAX_IMAGES))
    max_image_bytes = max(1, int(settings.MULTIMODAL_MAX_IMAGE_BYTES))
    max_total_bytes = max(max_image_bytes, int(settings.MULTIMODAL_MAX_TOTAL_IMAGE_BYTES))
    if len(attachments) > max_images:
        raise MultimodalInputError(f"At most {max_images} images may be attached")

    raw_images: list[bytes] = []
    total_bytes = 0
    for attachment in attachments:
        media_type = str(_get_value(attachment, "media_type", "") or "").strip().lower()
        data_url = str(_get_value(attachment, "data_url", "") or "").strip()
        raw = decode_image_data_url(
            data_url,
            expected_media_type=media_type,
            max_bytes=max_image_bytes,
        )
        total_bytes += len(raw)
        if total_bytes > max_total_bytes:
            raise MultimodalInputError(
                f"Combined image attachments exceed {max_total_bytes} bytes"
            )
        raw_images.append(raw)
    return embed_image_bytes(raw_images)


def describe_local_image(file_path: Path) -> dict[str, str]:
    media_type = IMAGE_MEDIA_TYPE_BY_SUFFIX.get(file_path.suffix.lower())
    if not media_type:
        raise MultimodalInputError(f"Unsupported image extension: {file_path.suffix}")

    max_image_bytes = max(1, int(settings.MULTIMODAL_MAX_IMAGE_BYTES))
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise MultimodalInputError(
            f"Failed to inspect image {file_path.name}: {exc}"
        ) from exc
    if file_size > max_image_bytes:
        raise MultimodalInputError(
            f"Image exceeds the {max_image_bytes}-byte attachment limit"
        )
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise MultimodalInputError(
            f"Failed to read image {file_path.name}: {exc}"
        ) from exc
    data_url = image_bytes_to_data_url(raw, media_type)
    decode_image_data_url(
        data_url,
        expected_media_type=media_type,
        max_bytes=max_image_bytes,
    )
    description = caption_image_data_url(
        name=file_path.name,
        media_type=media_type,
        data_url=data_url,
        purpose="knowledge",
    )
    return {
        "name": file_path.name,
        "media_type": media_type,
        "description": description,
    }


def build_multimodal_query(
    query: str,
    image_descriptions: list[dict[str, str]],
) -> str:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        normalized_query = "Analyze the attached image and answer with concrete technical guidance."
    if not image_descriptions:
        return normalized_query

    lines = [normalized_query, "", "[User-provided image context]"]
    for index, item in enumerate(image_descriptions, start=1):
        name = str(item.get("name", "") or f"image-{index}").strip()
        description = str(item.get("description", "") or "").strip()
        lines.append(f"{index}. {name}: {description}")
    return "\n".join(lines).strip()


def build_persisted_user_message(
    query: str,
    image_descriptions: list[dict[str, str]],
) -> str:
    normalized_query = str(query or "").strip() or "Analyze the attached image."
    if not image_descriptions:
        return normalized_query
    names = ", ".join(
        str(item.get("name", "") or "image").strip()
        for item in image_descriptions
    )
    return f"{normalized_query}\n\n[Attached images: {names}]"

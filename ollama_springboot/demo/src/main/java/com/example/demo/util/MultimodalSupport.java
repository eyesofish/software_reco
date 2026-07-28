package com.example.demo.util;

import com.example.demo.dto.ImageAttachment;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public final class MultimodalSupport {
    private static final int MAX_IMAGES = 4;
    private static final int MAX_DATA_URL_CHARS = 8_000_000;
    private static final Set<String> ALLOWED_MEDIA_TYPES = Set.of(
            "image/jpeg",
            "image/png",
            "image/webp"
    );

    private MultimodalSupport() {
    }

    public static List<ImageAttachment> normalizeAttachments(List<ImageAttachment> raw) {
        if (raw == null || raw.isEmpty()) {
            return List.of();
        }
        if (raw.size() > MAX_IMAGES) {
            throw new IllegalArgumentException("At most " + MAX_IMAGES + " images may be attached");
        }

        List<ImageAttachment> normalized = new ArrayList<>();
        for (int index = 0; index < raw.size(); index++) {
            ImageAttachment item = raw.get(index);
            if (item == null) {
                throw new IllegalArgumentException("Image attachment " + (index + 1) + " is missing");
            }
            String name = trimToNull(item.getName());
            String mediaType = trimToNull(item.getMediaType());
            String dataUrl = trimToNull(item.getDataUrl());
            if (name == null || mediaType == null || dataUrl == null) {
                throw new IllegalArgumentException("Image attachment fields are required");
            }

            mediaType = mediaType.toLowerCase(Locale.ROOT);
            if (!ALLOWED_MEDIA_TYPES.contains(mediaType)) {
                throw new IllegalArgumentException("Unsupported image media type: " + mediaType);
            }
            if (dataUrl.length() > MAX_DATA_URL_CHARS) {
                throw new IllegalArgumentException("Image attachment is too large");
            }
            if (!dataUrl.startsWith("data:" + mediaType + ";base64,")) {
                throw new IllegalArgumentException("Image data_url must match media_type");
            }
            normalized.add(new ImageAttachment(name, mediaType, dataUrl));
        }
        return List.copyOf(normalized);
    }

    public static boolean hasAttachments(List<ImageAttachment> images) {
        return images != null && !images.isEmpty();
    }

    public static String queryOrDefault(String query, List<ImageAttachment> images) {
        String normalized = trimToNull(query);
        if (normalized != null) {
            return normalized;
        }
        if (hasAttachments(images)) {
            return "Analyze the attached image and provide concrete technical guidance.";
        }
        return "";
    }

    public static String summarizeUserMessage(String query, List<ImageAttachment> images) {
        String normalizedQuery = queryOrDefault(query, images);
        if (!hasAttachments(images)) {
            return normalizedQuery;
        }
        String names = images.stream()
                .map(ImageAttachment::getName)
                .map(MultimodalSupport::trimToNull)
                .filter(value -> value != null)
                .reduce((left, right) -> left + ", " + right)
                .orElse("image");
        return normalizedQuery + "\n\n[Attached images: " + names + "]";
    }

    public static Map<String, Object> redactImagePayload(Map<String, Object> payload) {
        Map<String, Object> redacted = new LinkedHashMap<>(payload == null ? Map.of() : payload);
        Object rawImages = redacted.get("images");
        if (!(rawImages instanceof Iterable<?> iterable)) {
            return redacted;
        }

        List<Map<String, Object>> summaries = new ArrayList<>();
        for (Object raw : iterable) {
            if (raw instanceof ImageAttachment image) {
                summaries.add(imageSummary(image.getName(), image.getMediaType(), image.getDataUrl()));
                continue;
            }
            if (raw instanceof Map<?, ?> map) {
                summaries.add(imageSummary(
                        asText(map.get("name")),
                        asText(map.containsKey("media_type") ? map.get("media_type") : map.get("mediaType")),
                        asText(map.containsKey("data_url") ? map.get("data_url") : map.get("dataUrl"))
                ));
            }
        }
        redacted.put("images", summaries);
        return redacted;
    }

    public static List<Map<String, Object>> normalizeRetrievedImages(Object raw) {
        if (!(raw instanceof Iterable<?> iterable)) {
            return List.of();
        }
        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object item : iterable) {
            if (!(item instanceof Map<?, ?> map)) {
                continue;
            }
            String docId = trimToNull(asText(map.containsKey("doc_id") ? map.get("doc_id") : map.get("docId")));
            String url = trimToNull(asText(map.get("url")));
            if (docId == null || url == null) {
                continue;
            }
            Map<String, Object> image = new LinkedHashMap<>();
            image.put("doc_id", docId);
            image.put("filename", firstNonBlank(
                    asText(map.get("filename")),
                    docId
            ));
            image.put("media_type", firstNonBlank(
                    asText(map.containsKey("media_type") ? map.get("media_type") : map.get("mediaType")),
                    ""
            ));
            image.put("url", url);
            image.put("caption", firstNonBlank(asText(map.get("caption")), ""));
            Object score = map.get("score");
            image.put("score", score instanceof Number ? score : 0.0d);
            normalized.add(image);
        }
        return List.copyOf(normalized);
    }

    private static Map<String, Object> imageSummary(String name, String mediaType, String dataUrl) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("name", firstNonBlank(name, "image"));
        summary.put("media_type", firstNonBlank(mediaType, "unknown"));
        summary.put("data_url", "<redacted " + (dataUrl == null ? 0 : dataUrl.length()) + " chars>");
        return summary;
    }

    private static String asText(Object value) {
        return value == null ? null : value.toString();
    }

    private static String firstNonBlank(String... values) {
        if (values == null) {
            return null;
        }
        for (String value : values) {
            String normalized = trimToNull(value);
            if (normalized != null) {
                return normalized;
            }
        }
        return null;
    }

    private static String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim();
        return normalized.isEmpty() ? null : normalized;
    }
}

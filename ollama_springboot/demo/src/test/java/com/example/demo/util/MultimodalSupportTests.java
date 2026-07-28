package com.example.demo.util;

import com.example.demo.dto.ImageAttachment;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class MultimodalSupportTests {
    private static final String PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==";

    @Test
    void normalizesValidAttachmentsAndSupportsImageOnlyQueries() {
        List<ImageAttachment> images = MultimodalSupport.normalizeAttachments(List.of(
                new ImageAttachment(" error.png ", "IMAGE/PNG", PNG_DATA_URL)
        ));

        assertThat(images).hasSize(1);
        assertThat(images.getFirst().getName()).isEqualTo("error.png");
        assertThat(images.getFirst().getMediaType()).isEqualTo("image/png");
        assertThat(MultimodalSupport.queryOrDefault("", images))
                .contains("Analyze the attached image");
        assertThat(MultimodalSupport.summarizeUserMessage("", images))
                .contains("[Attached images: error.png]");
    }

    @Test
    void rejectsUnsupportedOrMismatchedAttachments() {
        assertThatThrownBy(() -> MultimodalSupport.normalizeAttachments(List.of(
                new ImageAttachment("error.gif", "image/gif", "data:image/gif;base64,AAAA")
        ))).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unsupported");

        assertThatThrownBy(() -> MultimodalSupport.normalizeAttachments(List.of(
                new ImageAttachment("error.png", "image/png", "data:image/jpeg;base64,AAAA")
        ))).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must match");
    }

    @Test
    void redactsBase64PayloadAndNormalizesRetrievedImages() {
        Map<String, Object> payload = Map.of(
                "query", "inspect",
                "images", List.of(new ImageAttachment("error.png", "image/png", PNG_DATA_URL))
        );

        Map<String, Object> redacted = MultimodalSupport.redactImagePayload(payload);
        assertThat(redacted.toString()).doesNotContain(PNG_DATA_URL);
        assertThat(redacted.toString()).contains("<redacted");

        List<Map<String, Object>> citations = MultimodalSupport.normalizeRetrievedImages(List.of(
                Map.of(
                        "doc_id", "screens/error.png",
                        "filename", "error.png",
                        "media_type", "image/png",
                        "url", "/api/v1/assets/screens/error.png",
                        "caption", "Connection refused",
                        "score", 0.1d
                )
        ));
        assertThat(citations).hasSize(1);
        assertThat(citations.getFirst())
                .containsEntry("doc_id", "screens/error.png")
                .containsEntry("url", "/api/v1/assets/screens/error.png");
    }
}

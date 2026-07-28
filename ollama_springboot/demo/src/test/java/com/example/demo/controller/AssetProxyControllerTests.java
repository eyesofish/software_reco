package com.example.demo.controller;

import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AssetProxyControllerTests {
    @Test
    void normalizesSafeNestedAssetPaths() {
        assertThat(AssetProxyController.normalizeAssetPath("/diagrams/system.png"))
                .isEqualTo("diagrams/system.png");
    }

    @Test
    void rejectsTraversalAndRemoteUrls() {
        assertThatThrownBy(() -> AssetProxyController.normalizeAssetPath("../secret.png"))
                .isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> AssetProxyController.normalizeAssetPath("https://example.com/a.png"))
                .isInstanceOf(ResponseStatusException.class);
    }
}

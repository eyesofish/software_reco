package com.example.demo.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.time.Duration;

import static org.springframework.http.HttpStatus.BAD_REQUEST;

@RestController
public class AssetProxyController {
    private final RestTemplate restTemplate;
    private final String fastApiBaseUrl;

    public AssetProxyController(
            RestTemplate restTemplate,
            @Value("${app.fastapi.base-url}") String fastApiBaseUrl
    ) {
        this.restTemplate = restTemplate;
        this.fastApiBaseUrl = fastApiBaseUrl;
    }

    @GetMapping("/api/v1/assets/{*assetPath}")
    public ResponseEntity<byte[]> getAsset(@PathVariable String assetPath) {
        String normalizedPath = normalizeAssetPath(assetPath);
        URI upstreamUri = UriComponentsBuilder
                .fromUriString(fastApiBaseUrl)
                .path("/api/v1/assets/")
                .path(normalizedPath)
                .build()
                .encode()
                .toUri();

        ResponseEntity<byte[]> upstream;
        try {
            upstream = restTemplate.getForEntity(upstreamUri, byte[].class);
        } catch (HttpStatusCodeException exc) {
            return ResponseEntity
                    .status(exc.getStatusCode())
                    .body(exc.getResponseBodyAsByteArray());
        }
        MediaType contentType = upstream.getHeaders().getContentType();
        byte[] body = upstream.getBody();
        ResponseEntity.BodyBuilder response = ResponseEntity
                .status(upstream.getStatusCode())
                .cacheControl(CacheControl.maxAge(Duration.ofMinutes(10)));
        if (contentType != null) {
            response.contentType(contentType);
        }
        if (body != null) {
            response.contentLength(body.length);
        }
        return response.body(body);
    }

    static String normalizeAssetPath(String assetPath) {
        String normalized = assetPath == null ? "" : assetPath.trim();
        while (normalized.startsWith("/")) {
            normalized = normalized.substring(1);
        }
        if (normalized.isBlank()
                || normalized.contains("..")
                || normalized.contains("\\")
                || normalized.startsWith("http:")
                || normalized.startsWith("https:")) {
            throw new ResponseStatusException(BAD_REQUEST, "Invalid asset path");
        }
        return normalized;
    }
}

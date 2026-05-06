package com.example.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import org.springframework.http.ResponseEntity;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

@RestController
public class StreamController {

    @Value("${proxy.upstream.url:http://localhost:8000/api/chat/stream}")
    private String upstreamUrl;

    @GetMapping("/proxy/chat/stream")
    public ResponseEntity<StreamingResponseBody> streamProxy() {
        StreamingResponseBody stream = outputStream -> {
            HttpURLConnection conn = null;
            try {
                URL url = new URL(upstreamUrl);
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");

                try (InputStream upstream = conn.getInputStream()) {
                    byte[] buffer = new byte[256];
                    int bytesRead;
                    while ((bytesRead = upstream.read(buffer)) != -1) {
                        outputStream.write(buffer, 0, bytesRead);
                        outputStream.flush();
                    }
                }
            } catch (Exception e) {
                String message = e.getMessage() == null ? "Streaming failed" : e.getMessage();
                String escaped = message
                        .replace("\\", "\\\\")
                        .replace("\"", "\\\"")
                        .replace("\r", " ")
                        .replace("\n", " ");
                String frame = "event: error\ndata: {\"type\":\"error\",\"message\":\"" + escaped + "\"}\n\n";
                outputStream.write(frame.getBytes(StandardCharsets.UTF_8));
                outputStream.flush();
            } finally {
                if (conn != null) {
                    conn.disconnect();
                }
            }
        };

        return ResponseEntity.ok()
                .header("Content-Type", "text/event-stream")
                .header("Cache-Control", "no-cache")
                .header("Connection", "keep-alive")
                .body(stream);
    }
}

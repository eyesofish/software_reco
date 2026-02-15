package com.example.demo.client;

import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class FastApiClient {
    private static final Logger logger = LoggerFactory.getLogger(FastApiClient.class);
    private final RestTemplate restTemplate;
    private final String recommendUrl;
    private final String sessionStateUrl;

    public FastApiClient(
            RestTemplate restTemplate,
            @Value("${app.fastapi.base-url}") String baseUrl,
            @Value("${app.fastapi.recommend-path}") String recommendPath,
            @Value("${app.fastapi.session-state-path:/api/v1/session-state}") String sessionStatePath) {
        this.restTemplate = restTemplate;
        this.recommendUrl = baseUrl + recommendPath;
        this.sessionStateUrl = baseUrl + sessionStatePath;
    }

    public RecommendResponse recommend(RecommendRequest request) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        Map<String, Object> payload = new HashMap<>();
        payload.put("query", request.getQuery());
        payload.put("timeout", request.getTimeout());
        payload.put("max_iterations", request.getMaxIterations());
        if (request.getSessionId() != null && !request.getSessionId().isBlank()) {
            payload.put("session_id", request.getSessionId());
        }

        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);

        logger.info(
                "FastAPI request url={}, payload={}, entity={}",
                recommendUrl, payload, entity);

        ResponseEntity<RecommendResponse> response = restTemplate.exchange(
                recommendUrl,
                HttpMethod.POST,
                entity,
                RecommendResponse.class);

        return response.getBody();
    }

    public Map<String, Object> getSessionState(String sessionId) {
        if (sessionId == null || sessionId.isBlank()) {
            return Map.of();
        }
        ResponseEntity<Map> response = restTemplate.exchange(
                sessionStateUrl + "/" + sessionId,
                HttpMethod.GET,
                new HttpEntity<>(new HttpHeaders()),
                Map.class
        );
        return response.getBody() == null ? Map.of() : response.getBody();
    }

    public void upsertSessionState(String sessionId, Map<String, String> facts) {
        if (sessionId == null || sessionId.isBlank()) {
            return;
        }
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        Map<String, Object> payload = new HashMap<>();
        payload.put("facts", facts == null ? Map.of() : facts);
        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);

        restTemplate.exchange(
                sessionStateUrl + "/" + sessionId,
                HttpMethod.PUT,
                entity,
                Map.class
        );
    }
}

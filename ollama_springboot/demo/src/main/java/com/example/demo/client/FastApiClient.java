package com.example.demo.client;

import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

@Component
public class FastApiClient {
    private static final Logger logger = LoggerFactory.getLogger(FastApiClient.class);
    private final RestTemplate restTemplate;
    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private final String recommendUrl;
    private final String recommendStreamUrl;
    private final String confirmUrl;
    private final String confirmStreamUrl;
    private final String sessionStateUrl;

    public FastApiClient(
            RestTemplate restTemplate,
            WebClient webClient,
            ObjectMapper objectMapper,
            @Value("${app.fastapi.base-url}") String baseUrl,
            @Value("${app.fastapi.recommend-path}") String recommendPath,
            @Value("${app.fastapi.recommend-stream-path:/api/v1/recommend/stream}") String recommendStreamPath,
            @Value("${app.fastapi.confirm-path:/api/v1/recommend/confirm}") String confirmPath,
            @Value("${app.fastapi.confirm-stream-path:/api/v1/recommend/confirm/stream}") String confirmStreamPath,
            @Value("${app.fastapi.session-state-path:/api/v1/session-state}") String sessionStatePath) {
        this.restTemplate = restTemplate;
        this.webClient = webClient;
        this.objectMapper = objectMapper;
        this.recommendUrl = baseUrl + recommendPath;
        this.recommendStreamUrl = baseUrl + recommendStreamPath;
        this.confirmUrl = baseUrl + confirmPath;
        this.confirmStreamUrl = baseUrl + confirmStreamPath;
        this.sessionStateUrl = baseUrl + sessionStatePath;
    }

    public RecommendResponse recommend(RecommendRequest request) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        Map<String, Object> payload = buildRecommendPayload(request);
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

    public void streamRecommend(
            RecommendRequest request,
            Consumer<RecommendStreamEvent> onEvent
    ) {
        Map<String, Object> payload = buildRecommendPayload(request);

        logger.info("FastAPI stream request url={}, payload={}", recommendStreamUrl, payload);

        Flux<ServerSentEvent<String>> events = webClient
                .post()
                .uri(recommendStreamUrl)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(payload)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<>() {
                });

        events.doOnNext(event -> {
                    String eventType = firstNonBlank(trimToNull(event.event()), "message");
                    Map<String, Object> eventPayload = decodeSsePayload(event.data(), eventType);
                    onEvent.accept(new RecommendStreamEvent(eventType, eventPayload));
                })
                .blockLast();
    }

    public void streamConfirm(
            String sessionId,
            String action,
            List<String> subQuestions,
            String comment,
            Consumer<RecommendStreamEvent> onEvent
    ) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("session_id", sessionId);
        payload.put("action", action);
        payload.put("sub_questions", subQuestions == null ? List.of() : subQuestions);
        payload.put("comment", comment == null ? "" : comment);

        logger.info("FastAPI confirm stream request url={}, payload={}", confirmStreamUrl, payload);

        Flux<ServerSentEvent<String>> events = webClient
                .post()
                .uri(confirmStreamUrl)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(payload)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<>() {
                });

        events.doOnNext(event -> {
                    String eventType = firstNonBlank(trimToNull(event.event()), "message");
                    Map<String, Object> eventPayload = decodeSsePayload(event.data(), eventType);
                    onEvent.accept(new RecommendStreamEvent(eventType, eventPayload));
                })
                .blockLast();
    }

    public RecommendResponse confirm(String sessionId, String action, List<String> subQuestions, String comment) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        Map<String, Object> payload = new HashMap<>();
        payload.put("session_id", sessionId);
        payload.put("action", action);
        payload.put("sub_questions", subQuestions == null ? List.of() : subQuestions);
        payload.put("comment", comment == null ? "" : comment);

        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);

        logger.info("FastAPI confirm request url={}, payload={}", confirmUrl, payload);

        ResponseEntity<RecommendResponse> response = restTemplate.exchange(
                confirmUrl,
                HttpMethod.POST,
                entity,
                RecommendResponse.class
        );
        RecommendResponse body = response.getBody();
        if (body == null) {
            throw new IllegalStateException("FastAPI confirm returned empty body");
        }
        logger.info(
                "FastAPI confirm response status={}, awaiting={}, pending_count={}, session_id={}",
                body.getStatus(),
                Boolean.TRUE.equals(body.getAwaitingHumanConfirmation()),
                countSubQuestionItems(body.getPendingSubQuestions()),
                body.getSessionId()
        );
        return body;
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

    private Map<String, Object> buildRecommendPayload(RecommendRequest request) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("query", request.getQuery());
        payload.put("timeout", request.getTimeout());
        payload.put("max_iterations", request.getMaxIterations());
        if (request.getSessionId() != null && !request.getSessionId().isBlank()) {
            payload.put("session_id", request.getSessionId());
        }
        return payload;
    }

    private Map<String, Object> decodeSsePayload(String rawData, String fallbackType) {
        Map<String, Object> payload = new LinkedHashMap<>();
        String normalized = trimToNull(rawData);
        if (normalized != null) {
            try {
                Object parsed = objectMapper.readValue(normalized, Object.class);
                if (parsed instanceof Map<?, ?> parsedMap) {
                    for (Map.Entry<?, ?> entry : parsedMap.entrySet()) {
                        String key = entry.getKey() == null ? "" : entry.getKey().toString();
                        if (key.isEmpty()) {
                            continue;
                        }
                        payload.put(key, entry.getValue());
                    }
                } else {
                    payload.put("value", parsed);
                }
            } catch (Exception ex) {
                payload.put("message", normalized);
            }
        }
        payload.putIfAbsent("type", fallbackType);
        return payload;
    }

    private int countSubQuestionItems(Object raw) {
        if (raw == null) {
            return 0;
        }
        Set<String> unique = new LinkedHashSet<>();
        collectSubQuestionItems(raw, unique, 0);
        return unique.size();
    }

    private void collectSubQuestionItems(Object raw, Set<String> output, int depth) {
        if (raw == null || depth > 8) {
            return;
        }
        if (raw instanceof String text) {
            String normalized = text.trim();
            if (!normalized.isEmpty()) {
                output.add(normalized);
            }
            return;
        }
        if (raw instanceof Map<?, ?> mapValue) {
            for (Object value : mapValue.values()) {
                collectSubQuestionItems(value, output, depth + 1);
            }
            return;
        }
        if (raw instanceof Iterable<?> iterable) {
            for (Object value : iterable) {
                collectSubQuestionItems(value, output, depth + 1);
            }
            return;
        }
        String normalized = raw.toString().trim();
        if (!normalized.isEmpty()) {
            output.add(normalized);
        }
    }

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim();
        return normalized.isEmpty() ? null : normalized;
    }

    private String firstNonBlank(String... values) {
        if (values == null || values.length == 0) {
            return null;
        }
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    public record RecommendStreamEvent(String type, Map<String, Object> payload) {
    }
}

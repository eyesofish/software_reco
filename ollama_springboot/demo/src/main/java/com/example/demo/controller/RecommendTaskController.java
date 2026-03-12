package com.example.demo.controller;

import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.ConversationCreateRequest;
import com.example.demo.dto.ConversationCreateResponse;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendTaskConfirmRequest;
import com.example.demo.dto.RecommendTaskCreateRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.lang.reflect.Array;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

@RestController
@RequestMapping("/api/v1")
public class RecommendTaskController {
    private static final Logger logger = LoggerFactory.getLogger(RecommendTaskController.class);

    private final RecommendTaskStateService recommendTaskStateService;
    private final FastApiClient fastApiClient;

    public RecommendTaskController(
            RecommendTaskStateService recommendTaskStateService,
            FastApiClient fastApiClient
    ) {
        this.recommendTaskStateService = recommendTaskStateService;
        this.fastApiClient = fastApiClient;
    }

    @PostMapping("/conversations")
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationCreateResponse createConversation(
            @RequestBody(required = false) ConversationCreateRequest request
    ) {
        String title = request == null ? null : request.getTitle();
        String modelName = request == null ? null : request.getModelName();
        ConversationCreateResponse response = recommendTaskStateService.createConversation(title, modelName);
        logger.info("v1 conversation created conversation_id={}", response.getConversationId());
        return response;
    }

    @PostMapping("/recommend")
    public RecommendTaskStateResponse recommend(@RequestBody RecommendTaskCreateRequest request) {
        if (request == null || request.getQuery() == null || request.getQuery().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "query is required");
        }

        String conversationId = firstNonBlank(request.getConversationId(), request.getSessionId());

        RecommendTaskStateService.RecommendTaskCreateOutcome outcome = recommendTaskStateService.createRecommendTask(
                conversationId,
                request.getQuery(),
                request.getTimeout(),
                request.getMaxIterations(),
                null
        );

        RecommendTaskStateResponse response = outcome.taskState();
        logger.info(
                "v1 recommend task created task_id={}, conversation_id={}, status={}, pending_count={}",
                response.getTaskId(),
                response.getConversationId(),
                response.getStatus(),
                response.getSubQuestions() == null ? 0 : response.getSubQuestions().size()
        );
        return response;
    }

    @PostMapping(value = "/recommend/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter recommendStream(@RequestBody RecommendTaskCreateRequest request) {
        if (request == null || request.getQuery() == null || request.getQuery().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "query is required");
        }

        String conversationId = firstNonBlank(request.getConversationId(), request.getSessionId());
        RecommendTaskStateResponse taskState = recommendTaskStateService.createStreamingTask(
                conversationId,
                request.getQuery(),
                request.getTimeout(),
                request.getMaxIterations(),
                null
        );
        String taskId = taskState.getTaskId();
        String resolvedConversationId = firstNonBlank(taskState.getConversationId(), conversationId);

        SseEmitter emitter = new SseEmitter(0L);

        Map<String, Object> bootstrap = new LinkedHashMap<>();
        bootstrap.put("type", "meta");
        bootstrap.put("task_id", taskId);
        bootstrap.put("conversation_id", resolvedConversationId);
        bootstrap.put("status", taskState.getStatus());
        sendSseEvent(emitter, "meta", bootstrap);

        CompletableFuture.runAsync(() -> {
            try {
                fastApiClient.streamRecommend(
                        new RecommendRequest(
                                request.getQuery(),
                                request.getTimeout() == null ? 60 : request.getTimeout(),
                                request.getMaxIterations() == null ? 3 : request.getMaxIterations(),
                                resolvedConversationId
                        ),
                        event -> {
                            Map<String, Object> payload = normalizeStreamPayload(
                                    event.type(),
                                    event.payload(),
                                    taskId,
                                    resolvedConversationId,
                                    resolvedConversationId
                            );
                            String eventType = firstNonBlank(asText(payload.get("type")), "state");
                            recommendTaskStateService.applyStreamEvent(taskId, eventType, payload);
                            sendSseEvent(emitter, eventType, payload);
                        }
                );
                emitter.complete();
            } catch (Exception ex) {
                String errorMessage = firstNonBlank(ex.getMessage(), ex.getClass().getSimpleName(), "stream failed");
                Map<String, Object> errorPayload = normalizeStreamPayload(
                        "error",
                        Map.of("message", errorMessage),
                        taskId,
                        resolvedConversationId,
                        resolvedConversationId
                );
                recommendTaskStateService.applyStreamEvent(taskId, "error", errorPayload);
                sendSseEvent(emitter, "error", errorPayload);
                emitter.completeWithError(ex);
            }
        });

        emitter.onCompletion(() ->
                logger.info("recommend stream completed task_id={}, conversation_id={}", taskId, resolvedConversationId));
        emitter.onTimeout(() ->
                logger.warn("recommend stream timeout task_id={}, conversation_id={}", taskId, resolvedConversationId));

        return emitter;
    }

    @PostMapping(value = "/recommend/confirm/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter confirmStream(
            @RequestParam("taskId") String taskId,
            @RequestParam(name = "action", defaultValue = "confirm") String action,
            @RequestBody(required = false) RecommendTaskConfirmRequest request
    ) {
        List<String> subQuestions = request == null ? List.of() : request.getSubQuestions();
        String comment = request == null ? null : request.getComment();

        RecommendTaskStateService.ConfirmStreamPreparation preparation;
        try {
            preparation = recommendTaskStateService.prepareConfirmStreamingTask(taskId, action, subQuestions, comment);
        } catch (IllegalArgumentException ex) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, ex.getMessage(), ex);
        } catch (IllegalStateException ex) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, ex.getMessage(), ex);
        }

        RecommendTaskStateResponse taskState = preparation.taskState();
        String resolvedTaskId = firstNonBlank(taskState.getTaskId(), taskId);
        String resolvedConversationId = firstNonBlank(taskState.getConversationId(), preparation.sessionId());
        String sessionId = firstNonBlank(preparation.sessionId(), resolvedConversationId);

        SseEmitter emitter = new SseEmitter(0L);

        Map<String, Object> bootstrap = new LinkedHashMap<>();
        bootstrap.put("type", "meta");
        bootstrap.put("task_id", resolvedTaskId);
        bootstrap.put("conversation_id", resolvedConversationId);
        bootstrap.put("session_id", sessionId);
        bootstrap.put("status", taskState.getStatus());
        sendSseEvent(emitter, "meta", bootstrap);

        CompletableFuture.runAsync(() -> {
            try {
                fastApiClient.streamConfirm(
                        sessionId,
                        preparation.action(),
                        preparation.subQuestions(),
                        preparation.comment(),
                        event -> {
                            Map<String, Object> payload = normalizeStreamPayload(
                                    event.type(),
                                    event.payload(),
                                    resolvedTaskId,
                                    resolvedConversationId,
                                    sessionId
                            );
                            String eventType = firstNonBlank(asText(payload.get("type")), "state");
                            recommendTaskStateService.applyStreamEvent(resolvedTaskId, eventType, payload);
                            sendSseEvent(emitter, eventType, payload);
                        }
                );
                emitter.complete();
            } catch (Exception ex) {
                String errorMessage = firstNonBlank(ex.getMessage(), ex.getClass().getSimpleName(), "stream failed");
                Map<String, Object> errorPayload = normalizeStreamPayload(
                        "error",
                        Map.of("message", errorMessage),
                        resolvedTaskId,
                        resolvedConversationId,
                        sessionId
                );
                recommendTaskStateService.applyStreamEvent(resolvedTaskId, "error", errorPayload);
                sendSseEvent(emitter, "error", errorPayload);
                emitter.completeWithError(ex);
            }
        });

        emitter.onCompletion(() ->
                logger.info("confirm stream completed task_id={}, conversation_id={}", resolvedTaskId, resolvedConversationId));
        emitter.onTimeout(() ->
                logger.warn("confirm stream timeout task_id={}, conversation_id={}", resolvedTaskId, resolvedConversationId));

        return emitter;
    }

    @PostMapping("/recommend/confirm")
    public RecommendTaskStateResponse confirm(
            @RequestParam("taskId") String taskId,
            @RequestParam(name = "action", defaultValue = "confirm") String action,
            @RequestBody(required = false) RecommendTaskConfirmRequest request
    ) {
        List<String> subQuestions = request == null ? List.of() : request.getSubQuestions();
        String comment = request == null ? null : request.getComment();

        logger.info(
                "v1 confirm request received task_id={}, action={}, pending_count={}",
                taskId,
                action,
                subQuestions == null ? 0 : subQuestions.size()
        );

        RecommendTaskStateResponse response = recommendTaskStateService.confirmTask(taskId, action, subQuestions, comment);

        logger.info(
                "v1 confirm accepted task_id={}, status={}, conversation_id={}",
                response.getTaskId(),
                response.getStatus(),
                response.getConversationId()
        );
        return response;
    }

    @GetMapping("/tasks/{taskId}")
    public RecommendTaskStateResponse getTaskState(@PathVariable String taskId) {
        return recommendTaskStateService.getTaskState(taskId);
    }

    @GetMapping("/conversations/{conversationId}/tasks")
    public List<RecommendTaskStateResponse> listConversationTasks(@PathVariable String conversationId) {
        return recommendTaskStateService.listConversationTasks(conversationId);
    }

    @GetMapping("/recommend/task/{taskId}")
    public RecommendTaskStateResponse getTaskStateCompatibility(@PathVariable String taskId) {
        return recommendTaskStateService.getTaskState(taskId);
    }

    private Map<String, Object> normalizeStreamPayload(
            String rawEventType,
            Map<String, Object> rawPayload,
            String taskId,
            String conversationId,
            String defaultSessionId
    ) {
        Map<String, Object> source = rawPayload == null ? Map.of() : rawPayload;
        String eventType = normalizeEventType(firstNonBlank(rawEventType, asText(source.get("type")), "state"));
        String resolvedConversationId = firstNonBlank(
                asText(source.get("conversation_id")),
                asText(source.get("conversationId")),
                conversationId
        );
        String resolvedSessionId = firstNonBlank(
                asText(source.get("session_id")),
                asText(source.get("sessionId")),
                asText(source.get("conversation_id")),
                asText(source.get("conversationId")),
                defaultSessionId,
                resolvedConversationId
        );

        Map<String, Object> normalized = new LinkedHashMap<>();
        normalized.put("type", eventType);
        normalized.put("task_id", taskId);
        normalized.put("conversation_id", resolvedConversationId);
        normalized.put("session_id", resolvedSessionId);

        switch (eventType) {
            case "meta" -> normalized.put(
                    "status",
                    firstNonBlank(asText(source.get("status")), "GENERATING")
            );
            case "node" -> {
                normalized.put("node", firstNonBlank(asText(source.get("node")), "unknown"));
                normalized.put("status", firstNonBlank(asText(source.get("status")), "end"));
                normalized.put(
                        "payload_keys",
                        normalizeStringList(
                                source.containsKey("payload_keys")
                                        ? source.get("payload_keys")
                                        : source.get("payloadKeys")
                        )
                );
            }
            case "token" -> {
                normalized.put(
                        "delta",
                        firstNonBlank(asText(source.get("delta")), asText(source.get("message")), "")
                );
                normalized.put("node", firstNonBlank(asText(source.get("node")), ""));
            }
            case "awaiting_confirmation" -> normalized.put(
                    "sub_questions",
                    normalizeStringList(
                            source.containsKey("sub_questions")
                                    ? source.get("sub_questions")
                                    : (source.containsKey("pending_sub_questions")
                                    ? source.get("pending_sub_questions")
                                    : (source.containsKey("subQuestions")
                                    ? source.get("subQuestions")
                                    : source.get("pendingSubQuestions")))
                    )
            );
            case "final" -> {
                normalized.put("status", firstNonBlank(asText(source.get("status")), "success"));
                normalized.put(
                        "final_answer",
                        firstNonBlank(
                                asText(source.get("final_answer")),
                                asText(source.get("finalResult")),
                                asText(source.get("message")),
                                ""
                        )
                );
                normalized.put(
                        "retrieved_doc_ids",
                        normalizeStringList(
                                source.containsKey("retrieved_doc_ids")
                                        ? source.get("retrieved_doc_ids")
                                        : source.get("retrievedDocIds")
                        )
                );
            }
            case "error" -> normalized.put(
                    "message",
                    firstNonBlank(asText(source.get("message")), "stream failed")
            );
            default -> {
                eventType = "state";
                normalized.put("type", eventType);
                if (source.get("payload") instanceof Map<?, ?> payloadMap) {
                    normalized.put("payload", payloadMap);
                } else {
                    Map<String, Object> payload = new LinkedHashMap<>(source);
                    payload.remove("type");
                    normalized.put("payload", payload);
                }
            }
        }

        return normalized;
    }

    private String normalizeEventType(String rawType) {
        String normalized = firstNonBlank(rawType, "state").toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "meta", "node", "token", "state", "awaiting_confirmation", "final", "error" -> normalized;
            default -> "state";
        };
    }

    private List<String> normalizeStringList(Object raw) {
        if (raw == null) {
            return List.of();
        }

        List<String> normalized = new ArrayList<>();
        if (raw instanceof Iterable<?> iterable) {
            for (Object item : iterable) {
                String value = firstNonBlank(asText(item));
                if (value != null) {
                    normalized.add(value);
                }
            }
            return normalized;
        }

        if (raw.getClass().isArray()) {
            int length = Array.getLength(raw);
            for (int i = 0; i < length; i++) {
                String value = firstNonBlank(asText(Array.get(raw, i)));
                if (value != null) {
                    normalized.add(value);
                }
            }
            return normalized;
        }

        String single = firstNonBlank(asText(raw));
        return single == null ? List.of() : List.of(single);
    }

    private void sendSseEvent(SseEmitter emitter, String eventType, Map<String, Object> payload) {
        try {
            emitter.send(
                    SseEmitter.event()
                            .name(eventType)
                            .data(payload, MediaType.APPLICATION_JSON)
            );
        } catch (IOException | IllegalStateException sendError) {
            logger.warn(
                    "stream send failed event_type={}, error={}",
                    eventType,
                    sendError.toString()
            );
            throw new RuntimeException(sendError);
        }
    }

    private String asText(Object value) {
        if (value == null) {
            return null;
        }
        return value.toString();
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
}

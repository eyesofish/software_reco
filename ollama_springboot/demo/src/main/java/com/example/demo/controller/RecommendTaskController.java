package com.example.demo.controller;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import com.example.demo.auth.BusinessAccess;
import com.example.demo.routing.RoutingPreflight;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.ConversationCreateRequest;
import com.example.demo.dto.ConversationCreateResponse;
import com.example.demo.dto.ImageAttachment;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendTaskConfirmRequest;
import com.example.demo.dto.RecommendTaskCreateRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import com.example.demo.util.MultimodalSupport;
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
import java.util.concurrent.atomic.AtomicBoolean;
import reactor.core.Disposable;

@RestController
@RequestMapping("/api/v1")
public class RecommendTaskController {
    private static final Logger logger = LoggerFactory.getLogger(RecommendTaskController.class);

    private final RecommendTaskStateService recommendTaskStateService;
    private final FastApiClient fastApiClient;
    private final BusinessAccess access;
    private final RoutingPreflight routing;

    public RecommendTaskController(
            RecommendTaskStateService recommendTaskStateService,
            FastApiClient fastApiClient,
            BusinessAccess access,
            RoutingPreflight routing
    ) {
        this.recommendTaskStateService = recommendTaskStateService;
        this.fastApiClient = fastApiClient;
        this.access = access;
        this.routing = routing;
    }

    @PostMapping("/conversations")
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationCreateResponse createConversation(
            @RequestBody(required = false) ConversationCreateRequest request,
            @AuthenticationPrincipal Jwt jwt
    ) {
        String title = request == null ? null : request.getTitle();
        String modelName = request == null ? null : request.getModelName();
        ConversationCreateResponse response = recommendTaskStateService.createConversation(jwt.getSubject(), title, modelName);
        logger.info("v1 conversation created conversation_id={}", response.getConversationId());
        return response;
    }

    @Transactional
    @PostMapping("/recommend")
    public RecommendTaskStateResponse recommend(@RequestBody RecommendTaskCreateRequest request, @AuthenticationPrincipal Jwt jwt) {
        if (request == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "request is required");
        }
        List<ImageAttachment> images = normalizeImages(request.getImages());
        String query = MultimodalSupport.queryOrDefault(request.getQuery(), images);
        if (query.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "query or image is required");
        }

        RoutingPreflight.Outcome route = routing.resolve(jwt.getSubject(), request.getQuery(), !images.isEmpty(),
                request.getRoutingMode(), request.getConversationId(), request.getSessionId(), request.getThreadId(),
                request.getCurrentConversationId(), request.getTimeout() == null ? 60 : request.getTimeout());
        if (route.needsClarification()) {
            RecommendTaskStateResponse response = new RecommendTaskStateResponse();
            response.setStatus("routing_clarification"); response.setClarification(route.clarification());
            return response;
        }
        String conversationId = route.conversationId();

        RecommendTaskStateService.RecommendTaskCreateOutcome outcome = recommendTaskStateService.createRecommendTask(
                jwt.getSubject(),
                conversationId,
                query,
                request.getTimeout(),
                request.getMaxIterations(),
                null,
                images,
                route.decision(), route.remainingSeconds()
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

    @Transactional
    @PostMapping(value = "/recommend/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter recommendStream(@RequestBody RecommendTaskCreateRequest request, @AuthenticationPrincipal Jwt jwt) {
        if (request == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "request is required");
        }
        List<ImageAttachment> images = normalizeImages(request.getImages());
        String query = MultimodalSupport.queryOrDefault(request.getQuery(), images);
        if (query.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "query or image is required");
        }

        RoutingPreflight.Outcome route = routing.resolve(jwt.getSubject(), request.getQuery(), !images.isEmpty(),
                request.getRoutingMode(), request.getConversationId(), request.getSessionId(), request.getThreadId(),
                request.getCurrentConversationId(), request.getTimeout() == null ? 60 : request.getTimeout());
        if (route.needsClarification()) {
            SseEmitter emitter = new SseEmitter(0L);
            sendSseEvent(emitter, "routing_clarification", route.clarificationPayload());
            emitter.complete(); return emitter;
        }
        String conversationId = route.conversationId();
        RecommendTaskStateResponse taskState = recommendTaskStateService.createStreamingTask(
                jwt.getSubject(),
                conversationId,
                query,
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
        SseStreamHandle stream = SseStreamHandle.start(emitter, () -> {
            Map<String, Object> disconnected = normalizeStreamPayload(
                    "error",
                    Map.of("message", "client disconnected; generation cancelled", "stop_reason", "cancelled"),
                    taskId,
                    resolvedConversationId,
                    resolvedConversationId
            );
            recommendTaskStateService.applyStreamEvent(taskId, "error", disconnected);
        });
        startAfterCommit(() -> {
            Disposable subscription = fastApiClient.streamRecommendCancellable(
                    new RecommendRequest(
                            query,
                            request.getTimeout() == null ? 60 : request.getTimeout(),
                            request.getMaxIterations() == null ? 3 : request.getMaxIterations(),
                            resolvedConversationId,
                            images
                    ).routed(route.decision(), route.remainingSeconds()),
                    event -> {
                        try {
                            Map<String, Object> payload = normalizeStreamPayload(
                                    event.type(), event.payload(), taskId, resolvedConversationId, resolvedConversationId
                            );
                            String eventType = firstNonBlank(asText(payload.get("type")), "state");
                            recommendTaskStateService.applyStreamEvent(taskId, eventType, payload);
                            stream.send(eventType, payload);
                            if ("awaiting_input".equals(eventType) || "awaiting_confirmation".equals(eventType)) stream.complete();
                        } catch (RuntimeException error) {
                            stream.fail(error);
                        }
                    },
                    error -> failStream(stream, taskId, error),
                    stream::complete
            );
            stream.attach(subscription);
        });

        return emitter;
    }

    @PostMapping(value = "/recommend/confirm/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter confirmStream(
            @RequestParam("taskId") String taskId,
            @RequestParam(name = "action", defaultValue = "confirm") String action,
            @RequestBody(required = false) RecommendTaskConfirmRequest request,
            @AuthenticationPrincipal Jwt jwt
    ) {
        access.task(jwt.getSubject(), taskId);
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
        SseStreamHandle stream = SseStreamHandle.start(emitter, () -> {
            Map<String, Object> disconnected = normalizeStreamPayload(
                    "error",
                    Map.of("message", "client disconnected; generation cancelled", "stop_reason", "cancelled"),
                    resolvedTaskId,
                    resolvedConversationId,
                    sessionId
            );
            recommendTaskStateService.applyStreamEvent(resolvedTaskId, "error", disconnected);
        });
        Disposable subscription = fastApiClient.streamConfirmCancellable(
                sessionId,
                preparation.action(),
                preparation.subQuestions(),
                preparation.comment(),
                event -> {
                    try {
                        Map<String, Object> payload = normalizeStreamPayload(
                                event.type(), event.payload(), resolvedTaskId, resolvedConversationId, sessionId
                        );
                        String eventType = firstNonBlank(asText(payload.get("type")), "state");
                        recommendTaskStateService.applyStreamEvent(resolvedTaskId, eventType, payload);
                        stream.send(eventType, payload);
        if ("awaiting_input".equals(eventType) || "awaiting_confirmation".equals(eventType)) stream.complete();
                    } catch (RuntimeException error) {
                        stream.fail(error);
                    }
                },
                error -> failStream(stream, resolvedTaskId, error),
                stream::complete
        );
        stream.attach(subscription);

        return emitter;
    }

    @PostMapping("/recommend/confirm")
    public RecommendTaskStateResponse confirm(
            @RequestParam("taskId") String taskId,
            @RequestParam(name = "action", defaultValue = "confirm") String action,
            @RequestBody(required = false) RecommendTaskConfirmRequest request,
            @AuthenticationPrincipal Jwt jwt
    ) {
        access.task(jwt.getSubject(), taskId);
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

    @PostMapping("/recommend/clarify")
    public RecommendTaskStateResponse clarify(@RequestBody Map<String, Object> request, @AuthenticationPrincipal Jwt jwt) {
        String taskId = requireText(request, "task_id");
        access.task(jwt.getSubject(), taskId);
        return recommendTaskStateService.clarifyTask(taskId, requireText(request, "clarification_id"), requireText(request, "answer"));
    }

    @PostMapping(value = "/recommend/clarify/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter clarifyStream(@RequestBody Map<String, Object> request, @AuthenticationPrincipal Jwt jwt) {
        String taskId = requireText(request, "task_id");
        access.task(jwt.getSubject(), taskId);
        var preparation = recommendTaskStateService.prepareClarification(taskId,
                requireText(request, "clarification_id"), requireText(request, "answer"));
        String conversationId = preparation.taskState().getConversationId();
        SseEmitter emitter = new SseEmitter(0L);
        AtomicBoolean accepted = new AtomicBoolean();
        AtomicBoolean terminal = new AtomicBoolean();
        SseStreamHandle stream = SseStreamHandle.start(emitter, () -> {
            synchronized (accepted) {
                if (!terminal.compareAndSet(false, true)) return;
                if (!accepted.get()) recommendTaskStateService.restoreClarification(preparation);
                else recommendTaskStateService.applyStreamEvent(taskId, "error",
                        Map.of("message", "client disconnected; generation cancelled", "stop_reason", "cancelled"));
            }
        });
        Disposable subscription = fastApiClient.streamClarifyCancellable(preparation.sessionId(),
                preparation.clarificationId(), preparation.answer(), event -> {
                    synchronized (accepted) {
                    if (terminal.get()) return;
                    try {
                        if ("resume_accepted".equals(event.type())) {
                            if (accepted.get()) return;
                            if (!preparation.clarificationId().equals(event.payload().get("clarification_id"))
                                    || !recommendTaskStateService.acceptClarification(preparation))
                                throw new IllegalStateException("Clarification acceptance does not match its reservation");
                            accepted.set(true);
                            stream.send("meta", normalizeStreamPayload("meta", Map.of("status", "GENERATING"),
                                    taskId, conversationId, preparation.sessionId()));
                            return;
                        }
                        if (!accepted.get()) throw new IllegalStateException("Agent did not accept the clarification resume");
                        Map<String, Object> payload = normalizeStreamPayload(event.type(), event.payload(), taskId,
                                conversationId, preparation.sessionId());
                        String type = String.valueOf(payload.get("type"));
                        recommendTaskStateService.applyStreamEvent(taskId, type, payload);
                        boolean finished = List.of("awaiting_input", "awaiting_confirmation", "final", "error").contains(type);
                        if (finished) terminal.set(true);
                        stream.send(type, payload);
                        if (finished) stream.complete();
                    } catch (RuntimeException error) {
                        terminal.set(true);
                        if (!accepted.get()) recommendTaskStateService.restoreClarification(preparation);
                        else recommendTaskStateService.applyStreamEvent(taskId, "error", Map.of("message", "Agent stream failed"));
                        stream.fail(error);
                    }
                    }
                }, error -> {
                    synchronized (accepted) {
                    if (!terminal.compareAndSet(false, true)) return;
                    if (!accepted.get()) recommendTaskStateService.restoreClarification(preparation);
                    else recommendTaskStateService.applyStreamEvent(taskId, "error", Map.of("message", "Agent stream failed"));
                    stream.fail(error);
                    }
                }, () -> {
                    synchronized (accepted) {
                    if (terminal.compareAndSet(false, true)) {
                        if (!accepted.get()) recommendTaskStateService.restoreClarification(preparation);
                        else recommendTaskStateService.applyStreamEvent(taskId, "error", Map.of("message", "Agent stream ended before completion"));
                        stream.fail(new IllegalStateException("Agent stream ended before completion"));
                    } else stream.complete();
                    }
                });
        stream.attach(subscription);
        return emitter;
    }

    @PostMapping("/recommend/tasks/{taskId}/cancel")
    public RecommendTaskStateResponse cancelWaiting(@PathVariable String taskId, @AuthenticationPrincipal Jwt jwt) {
        access.task(jwt.getSubject(), taskId);
        return recommendTaskStateService.cancelWaitingTask(taskId);
    }

    private String requireText(Map<String, Object> request, String key) {
        String value = request == null ? null : asText(request.get(key));
        if (value == null || value.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, key + " is required");
        return value.trim();
    }

    @GetMapping("/tasks/{taskId}")
    public RecommendTaskStateResponse getTaskState(@PathVariable String taskId, @AuthenticationPrincipal Jwt jwt) {
        return recommendTaskStateService.getOwnedTaskState(jwt.getSubject(), taskId);
    }

    @GetMapping("/conversations/{conversationId}/tasks")
    public List<RecommendTaskStateResponse> listConversationTasks(@PathVariable String conversationId, @AuthenticationPrincipal Jwt jwt) {
        access.conversation(jwt.getSubject(), conversationId);
        return recommendTaskStateService.listConversationTasks(conversationId);
    }

    @GetMapping("/recommend/task/{taskId}")
    public RecommendTaskStateResponse getTaskStateCompatibility(@PathVariable String taskId, @AuthenticationPrincipal Jwt jwt) {
        return recommendTaskStateService.getOwnedTaskState(jwt.getSubject(), taskId);
    }

    private void startAfterCommit(Runnable start) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) { start.run(); return; }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { start.run(); }
        });
    }

    private void failStream(SseStreamHandle stream, String taskId, Throwable error) {
        recommendTaskStateService.applyStreamEvent(taskId, "error", Map.of("message", "Agent stream failed"));
        stream.fail(error);
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
                Object delta = source.getOrDefault("delta", source.get("message"));
                normalized.put("delta", delta == null ? "" : String.valueOf(delta));
                normalized.put("node", firstNonBlank(asText(source.get("node")), ""));
            }
            case "routing_clarification", "awaiting_input", "route_decision" -> {
                normalized.putAll(source);
                normalized.put("type", eventType);
                normalized.put("task_id", taskId);
                normalized.put("conversation_id", resolvedConversationId);
                normalized.put("session_id", resolvedSessionId);
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
                if (source.get("stop_reason") != null) normalized.put("stop_reason", source.get("stop_reason"));
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
                normalized.put(
                        "retrieved_images",
                        MultimodalSupport.normalizeRetrievedImages(
                                source.containsKey("retrieved_images")
                                        ? source.get("retrieved_images")
                                        : source.get("retrievedImages")
                        )
                );
            }
            case "error" -> {
                normalized.put("message", firstNonBlank(asText(source.get("message")), "stream failed"));
                normalized.put("stop_reason", firstNonBlank(asText(source.get("stop_reason")), "execution_error"));
                if (source.get("run_id") != null) normalized.put("run_id", source.get("run_id"));
                if (source.get("elapsed_ms") != null) normalized.put("elapsed_ms", source.get("elapsed_ms"));
            }
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
            case "meta", "node", "token", "state", "awaiting_confirmation", "awaiting_input", "routing_clarification", "route_decision", "final", "error" -> normalized;
            default -> "state";
        };
    }

    private List<ImageAttachment> normalizeImages(List<ImageAttachment> images) {
        try {
            return MultimodalSupport.normalizeAttachments(images);
        } catch (IllegalArgumentException exc) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, exc.getMessage(), exc);
        }
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

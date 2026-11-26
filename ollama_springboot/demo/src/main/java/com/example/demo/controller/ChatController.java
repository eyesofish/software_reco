package com.example.demo.controller;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import com.example.demo.auth.BusinessAccess;
import com.example.demo.routing.RoutingPreflight;
import com.example.demo.conversation.service.ChatSubmissionService;
import com.example.demo.conversation.service.ChatSubmissionService.RequestKey;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import com.example.demo.client.FastApiClient;
import com.example.demo.client.FastApiClient.RecommendStreamEvent;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.service.ConversationService;
import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.Message;
import com.example.demo.dto.OllamaChatRequest;
import com.example.demo.dto.OllamaChatResponse;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import com.example.demo.dto.ImageAttachment;
import com.example.demo.util.MultimodalSupport;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.lang.reflect.Array;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
public class ChatController {
    private static final Logger logger = LoggerFactory.getLogger(ChatController.class);
    private static final Pattern[] SELF_INTRO_ZH_PATTERNS = new Pattern[]{
            Pattern.compile(
                    "(?:(?:^|[，,。！？!\\s])(?:\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u7684\\u540d\\u5b57\\u662f|\\u6211\\u7684\\u540d\\u5b57\\u662f)\\s*([\\p{IsHan}A-Za-z][\\p{IsHan}A-Za-z0-9_\\-]{0,31}))"
            ),
            Pattern.compile(
                    "(?:(?:^|[，,。！？!\\s])(?:\\u6211\\u662f)\\s*([\\p{IsHan}A-Za-z][\\p{IsHan}A-Za-z0-9_\\-]{0,31}))"
            )
    };
    private static final Pattern SELF_INTRO_EN_PATTERN = Pattern.compile(
            "(?i)(?:my name is|i am|i'm)\\s+([A-Za-z][A-Za-z\\-' ]{0,40})"
    );
    private static final Pattern[] USER_NAME_QUESTION_PATTERNS = new Pattern[]{
            Pattern.compile("\\u6211\\u53eb(\\u4ec0\\u4e48|\\u5565|\\u8c01)"),
            Pattern.compile("\\u6211\\u7684\\u540d\\u5b57(\\u662f)?(\\u4ec0\\u4e48|\\u5565|\\u8c01)"),
            Pattern.compile("\\u6211\\u662f\\u8c01"),
            Pattern.compile("(?i)what is my name"),
            Pattern.compile("(?i)who am i")
    };
    private static final Set<String> INVALID_NAME_VALUES = Set.of(
            "\u4ec0\u4e48", "\u8c01", "\u5565", "\u54ea\u4f4d", "\u540d\u5b57", "\u59d3\u540d", "name", "what", "who"
    );
    private static final Pattern CONFIRM_ONLY_PATTERN = Pattern.compile(
            "(?i)^\\s*(?:\\u786e\\u8ba4|\\u7ee7\\u7eed|\\u7ee7\\u7eed\\u5427|\\u597d\\u7684|\\u597d|ok|okay|yes|y|go on|continue)\\s*[.!?\\u3002\\uff01\\uff1f]*\\s*$"
    );
    private static final Pattern QUOTED_STRING_PATTERN = Pattern.compile(
            "'((?:\\\\.|[^'])*)'|\"((?:\\\\.|[^\"])*)\""
    );

    private final FastApiClient fastApiClient;
    private final BusinessAccess access;
    private final RoutingPreflight routing;
    private final ConversationService conversationService;
    private final RecommendTaskStateService recommendTaskStateService;
    private final TransactionTemplate transactions;
    private final ChatSubmissionService submissions;

    public ChatController(
            FastApiClient fastApiClient,
            BusinessAccess access,
            RoutingPreflight routing,
            ConversationService conversationService,
            RecommendTaskStateService recommendTaskStateService,
            PlatformTransactionManager transactionManager,
            ChatSubmissionService submissions
    ) {
        this.fastApiClient = fastApiClient;
        this.access = access;
        this.routing = routing;
        this.conversationService = conversationService;
        this.recommendTaskStateService = recommendTaskStateService;
        this.transactions = new TransactionTemplate(transactionManager);
        this.transactions.setTimeout(15);
        this.submissions = submissions;
    }

    @Transactional
    @PostMapping("/api/chat")
    public OllamaChatResponse chat(@RequestBody OllamaChatRequest request, @AuthenticationPrincipal Jwt jwt) {
        logIncomingMessages(request);
        String requestConversationId = null;

        List<ImageAttachment> images = normalizeImages(request.getImages());
        String rawQuery = extractLastUserMessage(request.getMessages());
        String query = MultimodalSupport.queryOrDefault(rawQuery, images);
        if (query.isBlank()) {
            return buildResponse(
                    request,
                    "Please enter a message.",
                    requestConversationId,
                    requestConversationId
            );
        }

        RoutingPreflight.Outcome route = routing.resolve(jwt.getSubject(), rawQuery, !images.isEmpty(),
                request.getRoutingMode(), request.getConversationId(), request.getSessionId(), request.getThreadId(),
                request.getCurrentConversationId(), 60);
        if (route.needsClarification()) {
            OllamaChatResponse response = buildResponse(request, String.valueOf(route.clarification().get("prompt")), null, null);
            response.setStatus("routing_clarification");
            response.setClarification(route.clarification());
            return response;
        }
        requestConversationId = route.conversationId();
        if (requestConversationId != null) recommendTaskStateService.assertConversationAvailable(jwt.getSubject(), requestConversationId);
        ConversationEntity conversation = conversationService.ensureOwnedConversation(
                jwt.getSubject(),
                requestConversationId,
                query,
                request.getModel()
        );

        String persistedUserMessage = MultimodalSupport.summarizeUserMessage(query, images);
        ConversationMessageEntity userMessage = conversationService.appendMessage(
                conversation,
                "user",
                persistedUserMessage
        );

        Map<String, String> extractedFacts = extractFactsForSessionUpdate(query);
        for (Map.Entry<String, String> fact : extractedFacts.entrySet()) {
            conversationService.upsertFact(
                    conversation.getId(),
                    fact.getKey(),
                    fact.getValue(),
                    userMessage.getId(),
                    0.99d
            );
        }

        Map<String, String> facts = conversationService.getFacts(conversation.getId());
        String knownName = facts.get("user_name");

        if (!MultimodalSupport.hasAttachments(images)
                && isAskingUserName(query)
                && knownName != null
                && !knownName.isBlank()) {
            String memoryAnswer = "\u4f60\u53eb" + knownName + "\u3002";
            conversationService.appendMessage(conversation, "assistant", memoryAnswer);
            return buildResponse(request, memoryAnswer, conversation.getId(), conversation.getId());
        }

        if (!MultimodalSupport.hasAttachments(images) && isConfirmOnlyInput(query)) {
            String blockedMessage = "Detected confirmation text. Please use the UI confirm button instead of sending a new question.";
            conversationService.appendMessage(conversation, "assistant", blockedMessage);
            return buildResponse(
                    request.getModel(),
                    blockedMessage,
                    conversation.getId(),
                    conversation.getId(),
                    "success",
                    false,
                    List.of()
            );
        }

        String enrichedQuery = buildQueryWithContext(
                query,
                facts,
                conversationService.getRecentMessages(conversation.getId(), 6)
        );

        try {
            fastApiClient.upsertSessionState(conversation.getId(), facts);
        } catch (Exception ex) {
            logger.warn("FastAPI session-state sync failed: {}", ex.getMessage());
        }

        RecommendTaskStateService.RecommendTaskCreateOutcome taskOutcome = recommendTaskStateService.createRecommendTask(
                jwt.getSubject(),
                conversation.getId(),
                enrichedQuery,
                60,
                3,
                request.getModel(),
                images,
                route.decision(), route.remainingSeconds()
        );
        RecommendTaskStateResponse taskState = taskOutcome.taskState();
        String status = firstNonBlank(taskState.getStatus(), "FAILED");
        boolean awaiting = "PENDING_CONFIRM".equalsIgnoreCase(status);
        List<String> pendingSubQuestions = normalizeSubQuestions(taskState.getSubQuestions());
        String content = firstNonBlank(taskOutcome.assistantMessage(), taskState.getFinalResult(), "");
        String sessionId = firstNonBlank(taskState.getTaskId(), conversation.getId());
        logger.info(
                "chat response status={}, awaiting={}, pending_count={}, task_id={}, conversation_id={}",
                status,
                awaiting,
                pendingSubQuestions.size(),
                sessionId,
                conversation.getId()
        );
        OllamaChatResponse response = buildResponse(
                request.getModel(),
                content,
                conversation.getId(),
                sessionId,
                status,
                awaiting,
                pendingSubQuestions,
                taskOutcome.retrievedImages()
        );
        response.setPendingInput(taskState.getPendingInput());
        return response;
    }

    @PostMapping(value = "/api/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter chatStream(@RequestBody OllamaChatRequest request, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Request-Id", required = false) String requestId) {
        String owner = jwt.getSubject();
        RequestKey key = submissions.key(requestId, request);
        String correlationId = requestId == null ? UUID.randomUUID().toString() : requestId;
        var gate = submissions.prepareOnce(owner, key, ChatPreparation.class,
                () -> routeAndPrepare(owner, key, correlationId, request));
        ChatPreparation result = gate.value();
        if (result instanceof ExistingChat existing) return submissions.replay(owner, existing.taskId());
        SseEmitter emitter = new SseEmitter(0L);
        if (result instanceof RoutingPrompt prompt) {
            sendSseEvent(emitter, "routing_clarification", prompt.payload());
            emitter.complete();
            return emitter;
        }
        PreparedChat chat = (PreparedChat) result;
        if (!gate.leader()) return submissions.replay(owner, chat.taskId());
        String conversationId = chat.conversationId();
        String taskId = chat.taskId();
        logger.info("chat.committed request_id={} conversation_id={} task_id={} stage=accept",
                correlationId, conversationId, taskId);
        sendSseEvent(emitter, "meta", normalizeStreamPayload("meta", Map.of("status", "GENERATING"),
                taskId, conversationId, conversationId));
        if (chat.localEvent() != null) {
            RecommendStreamEvent event = chat.localEvent();
            logger.info("chat.committed request_id={} conversation_id={} task_id={} stage=local_reply event={}",
                    correlationId, conversationId, taskId, event.type());
            sendSseEvent(emitter, event.type(), normalizeStreamPayload(event.type(), event.payload(),
                    taskId, conversationId, conversationId));
            emitter.complete();
            return emitter;
        }

        try {
            fastApiClient.upsertSessionState(conversationId, chat.facts());
        } catch (RuntimeException error) {
            // The request also carries its fact snapshot in the prompt.
            logChatFailure(correlationId, conversationId, taskId, "sync_facts", error);
        }
        ChatRelay relay = new ChatRelay(emitter, correlationId, conversationId, taskId, conversationId);
        try {
            relay.handle.attach(fastApiClient.streamRecommendCancellable(chat.agentRequest(),
                    relay::onEvent, relay::upstreamFailure, relay::upstreamClosed));
        } catch (RuntimeException error) {
            relay.upstreamFailure(error);
        }
        return emitter;
    }

    private sealed interface ChatPreparation permits PreparedChat, ExistingChat, RoutingPrompt { }
    private record ExistingChat(String taskId) implements ChatPreparation { }
    private record RoutingPrompt(Map<String, Object> payload) implements ChatPreparation { }
    private record PreparedChat(String taskId, String conversationId, Map<String, String> facts,
            RecommendRequest agentRequest, RecommendStreamEvent localEvent) implements ChatPreparation { }

    private ChatPreparation routeAndPrepare(String owner, RequestKey key, String correlationId, OllamaChatRequest request) {
        String original = submissions.findReplay(owner, key);
        if (original != null) return new ExistingChat(original);
        List<ImageAttachment> images = normalizeImages(request.getImages());
        String rawQuery = extractLastUserMessage(request.getMessages());
        String query = MultimodalSupport.queryOrDefault(rawQuery, images);
        if (query.isBlank()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "user message or image is required");
        RoutingPreflight.Outcome route = routing.resolve(owner, rawQuery, !images.isEmpty(),
                request.getRoutingMode(), request.getConversationId(), request.getSessionId(), request.getThreadId(),
                request.getCurrentConversationId(), 60);
        if (route.needsClarification()) return new RoutingPrompt(route.clarificationPayload());
        try {
            return transactions.execute(tx -> prepareChat(owner, key, request, route, query, images));
        } catch (RuntimeException error) {
            logChatFailure(correlationId, route.conversationId(), null, "accept", error);
            if (error instanceof ResponseStatusException statusError) throw statusError;
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "Could not accept chat. Reference: " + correlationId);
        }
    }

    private ChatPreparation prepareChat(String owner, RequestKey key, OllamaChatRequest request,
            RoutingPreflight.Outcome route, String query, List<ImageAttachment> images) {
        String replayId = submissions.reserve(owner, key);
        if (replayId != null) return new ExistingChat(replayId);
        RecommendTaskStateResponse task = recommendTaskStateService.createStreamingTask(owner, route.conversationId(), query,
                60, 3, request.getModel());
        String conversationId = task.getConversationId();
        String taskId = task.getTaskId();
        ConversationMessageEntity message = conversationService.appendMessage(conversationId, "user",
                MultimodalSupport.summarizeUserMessage(query, images));
        extractFactsForSessionUpdate(query).forEach((factKey, value) ->
                conversationService.upsertFact(conversationId, factKey, value, message.getId(), 0.99d));
        Map<String, String> facts = conversationService.getFacts(conversationId);
        submissions.bind(owner, key, taskId);
        RecommendStreamEvent local = null;
        String knownName = facts.get("user_name");
        if (images.isEmpty() && isAskingUserName(query) && knownName != null && !knownName.isBlank()) {
            local = new RecommendStreamEvent("final", Map.of("status", "success", "final_answer", "你叫" + knownName + "。"));
        } else if (images.isEmpty() && isConfirmOnlyInput(query)) {
            String hint = "Detected confirmation text. Please use the UI confirm button instead of sending a new question.";
            conversationService.appendMessage(conversationId, "assistant", hint);
            local = new RecommendStreamEvent("error", Map.of("message", hint));
        }
        if (local != null) {
            RecommendTaskStateResponse committed = recommendTaskStateService.applyStreamEvent(taskId, local.type(), local.payload());
            if ("final".equals(local.type()) && !"DONE".equals(committed.getStatus())) {
                local = new RecommendStreamEvent("error", Map.of("message", "Chat could not complete. Reference: " + taskId));
            }
            return new PreparedChat(taskId, conversationId, facts, null, local);
        }
        String agentQuery = buildQueryWithContext(query, facts, conversationService.getRecentMessages(conversationId, 6));
        RecommendRequest agentRequest = new RecommendRequest(agentQuery, 60, 3, conversationId, images)
                .routed(route.decision(), route.remainingSeconds());
        return new PreparedChat(taskId, conversationId, facts, agentRequest, null);
    }

    @PostMapping("/api/chat/confirm")
    public OllamaChatResponse confirm(@RequestBody Map<String, Object> request, @AuthenticationPrincipal Jwt jwt) {
        access.matchingConversation(jwt.getSubject(), asText(request.get("task_id")), asText(request.get("session_id")), asText(request.get("conversation_id")));
        String requestSessionId = firstNonBlank(
                asText(request.get("task_id")),
                asText(request.get("session_id")),
                asText(request.get("conversation_id"))
        );
        if (requestSessionId == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "task_id or session_id is required");
        }

        String resolvedTaskId = recommendTaskStateService.resolveTaskId(requestSessionId);
        if (resolvedTaskId == null) {
            RecommendTaskStateResponse expiredState = recommendTaskStateService.getTaskState(requestSessionId);
            return buildResponse(
                    firstNonBlank(asText(request.get("model")), "fastapi-hitl"),
                    firstNonBlank(expiredState.getErrorMessage(), ""),
                    firstNonBlank(asText(request.get("conversation_id")), requestSessionId),
                    requestSessionId,
                    firstNonBlank(expiredState.getStatus(), "EXPIRED"),
                    false,
                    List.of()
            );
        }

        RecommendTaskStateResponse currentState = recommendTaskStateService.getTaskState(resolvedTaskId);
        String conversationId = firstNonBlank(
                currentState.getConversationId(),
                asText(request.get("conversation_id")),
                requestSessionId
        );
        ConversationEntity conversation = conversationService.ensureOwnedConversation(
                jwt.getSubject(),
                conversationId,
                "HITL confirmation",
                asText(request.get("model"))
        );

        String action = normalizeConfirmAction(asText(request.get("action")));
        List<String> subQuestions = normalizeSubQuestions(request.get("sub_questions"));
        if ("edit".equals(action) && subQuestions.isEmpty()) {
            action = "confirm";
        }
        String comment = firstNonBlank(asText(request.get("comment")), "confirm");
        logger.info(
                "chat confirm request received session_id={}, conversation_id={}, action={}, pending_count={}",
                requestSessionId,
                conversation.getId(),
                action,
                subQuestions.size()
        );

        RecommendTaskStateResponse confirmedState = recommendTaskStateService.confirmTask(
                resolvedTaskId,
                action,
                subQuestions,
                comment
        );
        String status = firstNonBlank(confirmedState.getStatus(), "FAILED");
        boolean awaiting = "PENDING_CONFIRM".equalsIgnoreCase(status);
        List<String> pendingSubQuestions = normalizeSubQuestions(confirmedState.getSubQuestions());
        String content = firstNonBlank(confirmedState.getFinalResult(), "");
        String sessionId = firstNonBlank(confirmedState.getTaskId(), resolvedTaskId);
        logger.info(
                "chat confirm response status={}, awaiting={}, pending_count={}, task_id={}, action={}",
                status,
                awaiting,
                pendingSubQuestions.size(),
                sessionId,
                action
        );

        return buildResponse(
                firstNonBlank(asText(request.get("model")), conversation.getModelName(), "fastapi-hitl"),
                content,
                conversation.getId(),
                sessionId,
                status,
                awaiting,
                pendingSubQuestions
        );
    }

    @PostMapping(value = "/api/chat/confirm/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter confirmStream(@RequestBody Map<String, Object> request, @AuthenticationPrincipal Jwt jwt) {
        access.matchingConversation(jwt.getSubject(), asText(request.get("task_id")), asText(request.get("session_id")), asText(request.get("conversation_id")));
        String sessionId = firstNonBlank(
                asText(request.get("session_id")),
                asText(request.get("conversation_id")),
                asText(request.get("task_id"))
        );
        if (sessionId == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "session_id or conversation_id is required");
        }

        String action = normalizeConfirmAction(asText(request.get("action")));
        List<String> subQuestions = normalizeSubQuestions(request.get("sub_questions"));
        if ("edit".equals(action) && subQuestions.isEmpty()) {
            action = "confirm";
        }
        final String effectiveAction = action;
        String comment = firstNonBlank(asText(request.get("comment")), "confirm");
        String conversationId = access.referenceConversation(jwt.getSubject(), sessionId);
        String resolvedTask = recommendTaskStateService.resolveTaskId(sessionId);
        if (resolvedTask == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Resource not found");
        access.task(jwt.getSubject(), resolvedTask);
        RecommendTaskStateService.ConfirmStreamPreparation preparation;
        try {
            preparation = recommendTaskStateService.prepareConfirmStreamingTask(
                    resolvedTask, effectiveAction, subQuestions, comment);
        } catch (IllegalStateException ex) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, ex.getMessage());
        }
        String streamTaskId = resolvedTask;
        final String authorizedSessionId = preparation.sessionId();

        SseEmitter emitter = new SseEmitter(0L);
        sendSseEvent(emitter, "meta", normalizeStreamPayload("meta", Map.of("status", "GENERATING"),
                streamTaskId, conversationId, authorizedSessionId));
        ChatRelay relay = new ChatRelay(emitter, UUID.randomUUID().toString(), conversationId, streamTaskId, authorizedSessionId);
        try {
            relay.handle.attach(fastApiClient.streamConfirmCancellable(authorizedSessionId, preparation.action(),
                    preparation.subQuestions(), preparation.comment(), relay::onEvent, relay::upstreamFailure, relay::upstreamClosed));
        } catch (RuntimeException error) {
            relay.upstreamFailure(error);
        }
        return emitter;
    }

    private final class ChatRelay {
        private final String requestId;
        private final String conversationId;
        private final String taskId;
        private final String sessionId;
        private final SseStreamHandle handle;
        private final StringBuilder tokens = new StringBuilder();
        private long lastProgressWrite = System.nanoTime();
        private boolean finished;

        private ChatRelay(SseEmitter emitter, String requestId, String conversationId, String taskId, String sessionId) {
            this.requestId = requestId;
            this.conversationId = conversationId;
            this.taskId = taskId;
            this.sessionId = sessionId;
            this.handle = SseStreamHandle.start(emitter, this::disconnected);
        }

        private synchronized void onEvent(RecommendStreamEvent event) {
            if (finished) return;
            String type = normalizeEventType(event.type());
            try {
                Map<String, Object> payload = normalizeStreamPayload(type, event.payload(), taskId, conversationId, sessionId);
                if ("token".equals(type)) {
                    tokens.append(payload.get("delta"));
                    // Progress and heartbeat need a checkpoint, not a transaction per token.
                    long now = System.nanoTime();
                    if (now - lastProgressWrite < java.util.concurrent.TimeUnit.SECONDS.toNanos(4)) {
                        handle.send(type, payload);
                        return;
                    }
                    lastProgressWrite = now;
                } else if ("final".equals(type)) {
                    payload.put("final_answer", firstNonBlank(asText(payload.get("final_answer")), tokens.toString()));
                } else if ("error".equals(type)) {
                    payload.put("message", "Agent reported a generation error");
                }
                commitAndSend(type, payload);
            } catch (RuntimeException error) {
                fail("persist_" + type, error);
            }
        }

        private void commitAndSend(String type, Map<String, Object> payload) {
            RecommendTaskStateResponse state = recommendTaskStateService.applyStreamEvent(taskId, type, payload);
            String committedType;
            Map<String, Object> result = new LinkedHashMap<>();
            switch (state.getStatus()) {
                case "DONE", "INCOMPLETE" -> {
                    committedType = "final";
                    result.putAll(state.getResultMetadata());
                    result.put("status", "DONE".equals(state.getStatus()) ? "success" : "incomplete");
                    result.put("final_answer", state.getFinalResult());
                }
                case "PENDING_CONFIRM" -> {
                    committedType = "awaiting_confirmation";
                    result.put("sub_questions", state.getSubQuestions());
                }
                case "PENDING_INPUT" -> {
                    committedType = "awaiting_input";
                    result.put("pending_input", state.getPendingInput());
                }
                case "FAILED", "CANCELLED", "EXPIRED" -> {
                    committedType = "error";
                    result.put("message", "Chat could not complete. Reference: " + requestId);
                    result.put("stop_reason", state.getStatus().toLowerCase(Locale.ROOT));
                    if ("error".equals(type)) {
                        for (String key : List.of("stop_reason", "run_id", "elapsed_ms")) {
                            if (payload.get(key) != null) result.put(key, payload.get(key));
                        }
                    }
                }
                default -> {
                    handle.send(type, payload);
                    return;
                }
            }
            finished = true;
            logger.info("chat.committed request_id={} conversation_id={} task_id={} stage={} status={}",
                    requestId, conversationId, taskId, type, state.getStatus());
            Map<String, Object> response = normalizeStreamPayload(committedType, result, taskId, conversationId, sessionId);
            response.put("request_id", requestId);
            handle.send(committedType, response);
            handle.complete();
        }

        private synchronized void upstreamClosed() {
            if (finished) return;
            try {
                commitAndSend("final", Map.of("status", "incomplete", "final_answer", tokens.toString(),
                        "stop_reason", "upstream_closed"));
            } catch (RuntimeException error) {
                fail("persist_eof", error);
            }
        }

        private synchronized void upstreamFailure(Throwable error) {
            fail("upstream", error);
        }

        private synchronized void disconnected() {
            fail("disconnect", null);
        }

        private void fail(String stage, Throwable error) {
            if (finished) return;
            logChatFailure(requestId, conversationId, taskId, stage, error);
            try {
                commitAndSend("error", Map.of("message", "Chat failed at " + stage + " (request " + requestId + ")"));
            } catch (RuntimeException recordError) {
                finished = true;
                logChatFailure(requestId, conversationId, taskId, "persist_failure", recordError);
                handle.send("error", Map.of("type", "error", "task_id", taskId, "conversation_id", conversationId,
                        "request_id", requestId, "message", "Could not verify chat status. Reference: " + requestId));
                handle.complete();
            }
        }
    }

    private void logChatFailure(String requestId, String conversationId, String taskId, String stage, Throwable error) {
        // HTTP and SQL exception messages may contain user text or upstream response bodies.
        if (error instanceof ResponseStatusException rejected && rejected.getStatusCode().is4xxClientError()) {
            logger.info("chat.rejected request_id={} conversation_id={} task_id={} stage={} status={}",
                    requestId, conversationId, taskId, stage, rejected.getStatusCode().value());
            return;
        }
        String cause = error == null ? "client_disconnected" : error.getClass().getName();
        String sqlState = null;
        for (Throwable detail = error; detail != null; detail = detail.getCause()) {
            if (detail instanceof java.sql.SQLException sql) { sqlState = sql.getSQLState(); break; }
        }
        logger.warn("chat.failed request_id={} conversation_id={} task_id={} stage={} cause={} sql_state={} stack={}",
                requestId, conversationId, taskId, stage, cause, sqlState,
                error == null ? "" : java.util.Arrays.toString(error.getStackTrace()));
    }

    private void logIncomingMessages(OllamaChatRequest request) {
        if (request.getMessages() == null) {
            logger.info("Chat request messages is null. model={}, stream={}", request.getModel(), request.getStream());
        } else {
            for (int i = 0; i < request.getMessages().size(); i++) {
                Message message = request.getMessages().get(i);
                logger.info(
                        "Chat message[{}]: role={}",
                        i,
                        message.getRole()
                );
            }
        }
        if (MultimodalSupport.hasAttachments(request.getImages())) {
            logger.info(
                    "Chat request contains {} image attachment(s): {}",
                    request.getImages().size(),
                    request.getImages().stream()
                            .map(image -> firstNonBlank(image.getName(), "image"))
                            .toList()
            );
        }
    }

    private String extractLastUserMessage(List<Message> messages) {
        if (messages == null || messages.isEmpty()) {
            return "";
        }
        for (int i = messages.size() - 1; i >= 0; i--) {
            if ("user".equalsIgnoreCase(messages.get(i).getRole())) {
                return messages.get(i).getContent();
            }
        }
        return messages.get(messages.size() - 1).getContent();
    }

    private List<ImageAttachment> normalizeImages(List<ImageAttachment> images) {
        try {
            return MultimodalSupport.normalizeAttachments(images);
        } catch (IllegalArgumentException exc) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, exc.getMessage(), exc);
        }
    }

    private Optional<String> extractUserName(String query) {
        if (query == null || query.isBlank()) {
            return Optional.empty();
        }
        if (isAskingUserName(query)) {
            return Optional.empty();
        }

        for (Pattern pattern : SELF_INTRO_ZH_PATTERNS) {
            Matcher zh = pattern.matcher(query);
            if (zh.find()) {
                Optional<String> normalized = normalizeCandidateName(zh.group(1));
                if (normalized.isPresent()) {
                    return normalized;
                }
            }
        }

        Matcher en = SELF_INTRO_EN_PATTERN.matcher(query);
        if (en.find()) {
            return normalizeCandidateName(en.group(1));
        }

        return Optional.empty();
    }

    private Optional<String> normalizeCandidateName(String raw) {
        if (raw == null) {
            return Optional.empty();
        }

        String cleaned = raw.trim().replaceAll(
                "^[\\s，,。！？!?.；;:：\"'“”‘’()（）\\[\\]【】]+|[\\s，,。！？!?.；;:：\"'“”‘’()（）\\[\\]【】]+$",
                ""
        );
        if (cleaned.isEmpty()) {
            return Optional.empty();
        }

        String lower = cleaned.toLowerCase(Locale.ROOT);
        if (INVALID_NAME_VALUES.contains(cleaned) || INVALID_NAME_VALUES.contains(lower)) {
            return Optional.empty();
        }

        if (cleaned.endsWith("\u5417") || cleaned.endsWith("\u5462") || cleaned.endsWith("\u4e48") || cleaned.endsWith("\u561b")) {
            return Optional.empty();
        }

        return Optional.of(cleaned);
    }

    private Map<String, String> extractFactsForSessionUpdate(String query) {
        Map<String, String> facts = new LinkedHashMap<>();
        extractUserName(query).ifPresent(name -> facts.put("user_name", name));
        return facts;
    }

    private boolean isAskingUserName(String query) {
        if (query == null) {
            return false;
        }
        String normalized = query.trim();
        for (Pattern pattern : USER_NAME_QUESTION_PATTERNS) {
            if (pattern.matcher(normalized).find()) {
                return true;
            }
        }
        String lowered = normalized.toLowerCase(Locale.ROOT);
        return lowered.contains("what's my name") || lowered.contains("whats my name");
    }

    private String buildQueryWithContext(
            String query,
            Map<String, String> facts,
            List<ConversationMessageEntity> recentMessages
    ) {
        if ((facts == null || facts.isEmpty()) && (recentMessages == null || recentMessages.isEmpty())) {
            return capForRecommend(query);
        }

        StringBuilder sb = new StringBuilder();
        sb.append("Current user input:\n").append(query.trim());

        if (facts != null && !facts.isEmpty()) {
            sb.append("\n\nKnown user facts:\n");
            facts.forEach((k, v) -> sb.append("- ").append(k).append(": ").append(v).append("\n"));
            sb.append("Use these facts when answering identity-related questions.\n");
        }

        if (recentMessages != null && !recentMessages.isEmpty()) {
            List<ConversationMessageEntity> ordered = new ArrayList<>(recentMessages);
            Collections.reverse(ordered);
            sb.append("\nRecent conversation history:\n");
            for (ConversationMessageEntity message : ordered) {
                sb.append("- ")
                        .append(message.getRole())
                        .append(": ")
                        .append(compact(message.getContent()))
                        .append("\n");
            }
        }

        String built = sb.toString();
        return capForRecommend(built);
    }

    private String capForRecommend(String value) {
        if (value == null) {
            return "";
        }
        String normalized = value.trim();
        return normalized.length() > 980 ? normalized.substring(0, 980) : normalized;
    }

    private String compact(String text) {
        if (text == null) {
            return "";
        }
        String normalized = text.replace('\n', ' ').replace('\r', ' ').trim();
        return normalized.length() > 300 ? normalized.substring(0, 300) : normalized;
    }

    private boolean isConfirmOnlyInput(String text) {
        if (text == null) {
            return false;
        }
        return CONFIRM_ONLY_PATTERN.matcher(text.trim()).matches();
    }

    private String normalizeConfirmAction(String action) {
        String normalized = firstNonBlank(action, "confirm");
        if (normalized == null) {
            return "confirm";
        }
        normalized = normalized.trim().toLowerCase(Locale.ROOT);
        if ("edit".equals(normalized)) {
            return "edit";
        }
        return "confirm";
    }

    private List<String> normalizeSubQuestions(List<String> raw) {
        return normalizeSubQuestions((Object) raw);
    }

    private List<String> normalizeSubQuestions(Object raw) {
        if (raw == null) {
            return List.of();
        }

        LinkedHashSet<String> unique = new LinkedHashSet<>();
        collectSubQuestions(raw, unique, 0);
        return new ArrayList<>(unique);
    }

    private void collectSubQuestions(Object raw, Set<String> output, int depth) {
        if (raw == null || depth > 12) {
            return;
        }

        if (raw instanceof String textValue) {
            String text = firstNonBlank(textValue);
            if (text == null) {
                return;
            }

            if (looksLikeSerializedSubQuestionPayload(text)) {
                List<String> extracted = extractQuotedSegments(text);
                if (!extracted.isEmpty()) {
                    output.addAll(extracted);
                    return;
                }
            }

            output.add(text);
            return;
        }

        if (raw instanceof Map<?, ?> mapValue) {
            List<Object> preferred = new ArrayList<>();
            for (Map.Entry<?, ?> entry : mapValue.entrySet()) {
                String key = firstNonBlank(asText(entry.getKey()));
                if (key == null) {
                    continue;
                }
                String normalizedKey = key.toLowerCase(Locale.ROOT);
                if ("sub_questions".equals(normalizedKey) || "pending_sub_questions".equals(normalizedKey)) {
                    preferred.add(entry.getValue());
                }
            }

            if (!preferred.isEmpty()) {
                for (Object value : preferred) {
                    collectSubQuestions(value, output, depth + 1);
                }
                return;
            }

            for (Object value : mapValue.values()) {
                collectSubQuestions(value, output, depth + 1);
            }
            return;
        }

        if (raw instanceof Iterable<?> iterable) {
            for (Object item : iterable) {
                collectSubQuestions(item, output, depth + 1);
            }
            return;
        }

        if (raw.getClass().isArray()) {
            int length = Array.getLength(raw);
            for (int i = 0; i < length; i++) {
                collectSubQuestions(Array.get(raw, i), output, depth + 1);
            }
            return;
        }

        String text = firstNonBlank(asText(raw));
        if (text != null) {
            output.add(text);
        }
    }

    private boolean looksLikeSerializedSubQuestionPayload(String text) {
        String normalized = firstNonBlank(text);
        if (normalized == null) {
            return false;
        }
        if (!normalized.startsWith("{") && !normalized.startsWith("[")) {
            return false;
        }
        String lowered = normalized.toLowerCase(Locale.ROOT);
        return lowered.contains("sub_questions") || lowered.contains("pending_sub_questions");
    }

    private List<String> extractQuotedSegments(String raw) {
        List<String> extracted = new ArrayList<>();
        Matcher matcher = QUOTED_STRING_PATTERN.matcher(raw);
        while (matcher.find()) {
            String candidate = matcher.group(1) != null ? matcher.group(1) : matcher.group(2);
            String text = firstNonBlank(candidate);
            if (text == null) {
                continue;
            }
            String lowered = text.toLowerCase(Locale.ROOT);
            if ("sub_questions".equals(lowered) || "pending_sub_questions".equals(lowered)) {
                continue;
            }
            extracted.add(text);
        }
        return extracted;
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

    private Map<String, Object> normalizeStreamPayload(
            String rawEventType,
            Map<String, Object> rawPayload,
            String taskId,
            String conversationId,
            String defaultSessionId
    ) {
        Map<String, Object> source = rawPayload == null ? Map.of() : rawPayload;
        String eventType = normalizeEventType(firstNonBlank(rawEventType, asText(source.get("type")), "state"));
        String resolvedConversationId = conversationId;
        String resolvedSessionId = firstNonBlank(defaultSessionId, conversationId);

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
                if (source.get("run_id") != null) normalized.put("run_id", source.get("run_id"));
                if (source.get("elapsed_ms") != null) normalized.put("elapsed_ms", source.get("elapsed_ms"));
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
                    "chat stream send failed event_type={}, error={}",
                    eventType,
                    sendError.toString()
            );
            throw new RuntimeException(sendError);
        }
    }

    private OllamaChatResponse buildResponse(
            String model,
            String content,
            String conversationId,
            String sessionId,
            String status,
            boolean awaitingHumanConfirmation,
            List<String> pendingSubQuestions
    ) {
        return buildResponse(
                model,
                content,
                conversationId,
                sessionId,
                status,
                awaitingHumanConfirmation,
                pendingSubQuestions,
                List.of()
        );
    }

    private OllamaChatResponse buildResponse(
            String model,
            String content,
            String conversationId,
            String sessionId,
            String status,
            boolean awaitingHumanConfirmation,
            List<String> pendingSubQuestions,
            List<Map<String, Object>> retrievedImages
    ) {
        Message message = new Message();
        message.setRole("assistant");
        message.setContent(content);

        OllamaChatResponse response = new OllamaChatResponse();
        response.setModel(model);
        response.setMessage(message);
        response.setDone(true);
        response.setStatus(status);
        response.setAwaitingHumanConfirmation(awaitingHumanConfirmation);
        response.setPendingSubQuestions(pendingSubQuestions == null ? List.of() : pendingSubQuestions);
        response.setConversationId(conversationId);
        response.setSessionId(sessionId);
        response.setRetrievedImages(retrievedImages == null ? List.of() : retrievedImages);
        return response;
    }

    private OllamaChatResponse buildResponse(
            OllamaChatRequest request,
            String content,
            String conversationId,
            String sessionId
    ) {
        return buildResponse(
                request.getModel(),
                content,
                conversationId,
                sessionId,
                "success",
                false,
                List.of()
        );
    }
}

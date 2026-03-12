package com.example.demo.controller;

import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.service.ConversationService;
import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.Message;
import com.example.demo.dto.OllamaChatRequest;
import com.example.demo.dto.OllamaChatResponse;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
import java.util.concurrent.CompletableFuture;
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
    private final ConversationService conversationService;
    private final RecommendTaskStateService recommendTaskStateService;

    public ChatController(
            FastApiClient fastApiClient,
            ConversationService conversationService,
            RecommendTaskStateService recommendTaskStateService
    ) {
        this.fastApiClient = fastApiClient;
        this.conversationService = conversationService;
        this.recommendTaskStateService = recommendTaskStateService;
    }

    @PostMapping("/api/chat")
    public OllamaChatResponse chat(@RequestBody OllamaChatRequest request) {
        logIncomingMessages(request);
        String requestConversationId = firstNonBlank(request.getConversationId(), request.getSessionId());

        String query = extractLastUserMessage(request.getMessages());
        if (query == null || query.isBlank()) {
            return buildResponse(
                    request,
                    "Please enter a message.",
                    requestConversationId,
                    requestConversationId
            );
        }

        ConversationEntity conversation = conversationService.ensureConversation(
                requestConversationId,
                query,
                request.getModel()
        );

        ConversationMessageEntity userMessage = conversationService.appendMessage(conversation, "user", query);

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

        if (isAskingUserName(query) && knownName != null && !knownName.isBlank()) {
            String memoryAnswer = "\u4f60\u53eb" + knownName + "\u3002";
            conversationService.appendMessage(conversation, "assistant", memoryAnswer);
            return buildResponse(request, memoryAnswer, conversation.getId(), conversation.getId());
        }

        if (isConfirmOnlyInput(query)) {
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
                conversation.getId(),
                enrichedQuery,
                60,
                3,
                request.getModel()
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
        return buildResponse(
                request.getModel(),
                content,
                conversation.getId(),
                sessionId,
                status,
                awaiting,
                pendingSubQuestions
        );
    }

    @PostMapping(value = "/api/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter chatStream(@RequestBody OllamaChatRequest request) {
        logIncomingMessages(request);

        String requestConversationId = firstNonBlank(request.getConversationId(), request.getSessionId());
        String query = extractLastUserMessage(request.getMessages());
        if (query == null || query.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "user message is required");
        }

        ConversationEntity conversation = conversationService.ensureConversation(
                requestConversationId,
                query,
                request.getModel()
        );

        ConversationMessageEntity userMessage = conversationService.appendMessage(conversation, "user", query);
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
        String streamTaskId = firstNonBlank(request.getSessionId(), requestConversationId, conversation.getId());

        SseEmitter emitter = new SseEmitter(0L);
        Map<String, Object> bootstrap = normalizeStreamPayload(
                "meta",
                Map.of("status", "GENERATING"),
                streamTaskId,
                conversation.getId(),
                conversation.getId()
        );
        sendSseEvent(emitter, "meta", bootstrap);

        if (isAskingUserName(query) && knownName != null && !knownName.isBlank()) {
            String memoryAnswer = "你叫" + knownName + "。";
            conversationService.appendMessage(conversation, "assistant", memoryAnswer);
            Map<String, Object> finalPayload = normalizeStreamPayload(
                    "final",
                    Map.of(
                            "status", "success",
                            "final_answer", memoryAnswer
                    ),
                    streamTaskId,
                    conversation.getId(),
                    conversation.getId()
            );
            sendSseEvent(emitter, "final", finalPayload);
            emitter.complete();
            return emitter;
        }

        if (isConfirmOnlyInput(query)) {
            String blockedMessage = "Detected confirmation text. Please use the UI confirm button instead of sending a new question.";
            conversationService.appendMessage(conversation, "assistant", blockedMessage);
            Map<String, Object> errorPayload = normalizeStreamPayload(
                    "error",
                    Map.of("message", blockedMessage),
                    streamTaskId,
                    conversation.getId(),
                    conversation.getId()
            );
            sendSseEvent(emitter, "error", errorPayload);
            emitter.complete();
            return emitter;
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

        CompletableFuture.runAsync(() -> {
            StringBuilder tokenBuffer = new StringBuilder();
            try {
                fastApiClient.streamRecommend(
                        new RecommendRequest(
                                enrichedQuery,
                                60,
                                3,
                                conversation.getId()
                        ),
                        event -> {
                            Map<String, Object> payload = normalizeStreamPayload(
                                    event.type(),
                                    event.payload(),
                                    streamTaskId,
                                    conversation.getId(),
                                    conversation.getId()
                            );
                            String eventType = firstNonBlank(asText(payload.get("type")), "state");
                            if ("token".equals(eventType)) {
                                tokenBuffer.append(firstNonBlank(asText(payload.get("delta")), ""));
                            }
                            if ("final".equals(eventType)) {
                                String finalAnswer = firstNonBlank(
                                        asText(payload.get("final_answer")),
                                        tokenBuffer.toString()
                                );
                                if (finalAnswer != null && !finalAnswer.isBlank()) {
                                    conversationService.appendMessage(conversation, "assistant", finalAnswer);
                                }
                            }
                            sendSseEvent(emitter, eventType, payload);
                        }
                );
                emitter.complete();
            } catch (Exception ex) {
                String errorMessage = firstNonBlank(ex.getMessage(), ex.getClass().getSimpleName(), "stream failed");
                Map<String, Object> errorPayload = normalizeStreamPayload(
                        "error",
                        Map.of("message", errorMessage),
                        streamTaskId,
                        conversation.getId(),
                        conversation.getId()
                );
                sendSseEvent(emitter, "error", errorPayload);
                emitter.complete();
            }
        });

        emitter.onCompletion(() ->
                logger.info("chat stream completed conversation_id={}", conversation.getId()));
        emitter.onTimeout(() ->
                logger.warn("chat stream timeout conversation_id={}", conversation.getId()));

        return emitter;
    }

    @PostMapping("/api/chat/confirm")
    public OllamaChatResponse confirm(@RequestBody Map<String, Object> request) {
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
        ConversationEntity conversation = conversationService.ensureConversation(
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
    public SseEmitter confirmStream(@RequestBody Map<String, Object> request) {
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
        String conversationId = firstNonBlank(asText(request.get("conversation_id")), sessionId);
        String streamTaskId = firstNonBlank(asText(request.get("task_id")), sessionId, conversationId);

        ConversationEntity conversation = conversationService.ensureConversation(
                conversationId,
                "HITL confirmation",
                asText(request.get("model"))
        );

        SseEmitter emitter = new SseEmitter(0L);
        Map<String, Object> bootstrap = normalizeStreamPayload(
                "meta",
                Map.of("status", "GENERATING"),
                streamTaskId,
                conversation.getId(),
                sessionId
        );
        sendSseEvent(emitter, "meta", bootstrap);

        CompletableFuture.runAsync(() -> {
            StringBuilder tokenBuffer = new StringBuilder();
            try {
                fastApiClient.streamConfirm(
                        sessionId,
                        effectiveAction,
                        subQuestions,
                        comment,
                        event -> {
                            Map<String, Object> payload = normalizeStreamPayload(
                                    event.type(),
                                    event.payload(),
                                    streamTaskId,
                                    conversation.getId(),
                                    sessionId
                            );
                            String eventType = firstNonBlank(asText(payload.get("type")), "state");
                            if ("token".equals(eventType)) {
                                tokenBuffer.append(firstNonBlank(asText(payload.get("delta")), ""));
                            }
                            if ("final".equals(eventType)) {
                                String finalAnswer = firstNonBlank(
                                        asText(payload.get("final_answer")),
                                        tokenBuffer.toString()
                                );
                                if (finalAnswer != null && !finalAnswer.isBlank()) {
                                    conversationService.appendMessage(conversation, "assistant", finalAnswer);
                                }
                            }
                            sendSseEvent(emitter, eventType, payload);
                        }
                );
                emitter.complete();
            } catch (Exception ex) {
                String errorMessage = firstNonBlank(ex.getMessage(), ex.getClass().getSimpleName(), "stream failed");
                Map<String, Object> errorPayload = normalizeStreamPayload(
                        "error",
                        Map.of("message", errorMessage),
                        streamTaskId,
                        conversation.getId(),
                        sessionId
                );
                sendSseEvent(emitter, "error", errorPayload);
                emitter.complete();
            }
        });

        emitter.onCompletion(() ->
                logger.info("chat confirm stream completed conversation_id={}", conversation.getId()));
        emitter.onTimeout(() ->
                logger.warn("chat confirm stream timeout conversation_id={}", conversation.getId()));

        return emitter;
    }

    private void logIncomingMessages(OllamaChatRequest request) {
        if (request.getMessages() == null) {
            logger.info("Chat request messages is null. model={}, stream={}", request.getModel(), request.getStream());
            return;
        }
        for (int i = 0; i < request.getMessages().size(); i++) {
            Message message = request.getMessages().get(i);
            logger.info(
                    "Chat message[{}]: role={}, content={}",
                    i,
                    message.getRole(),
                    message.getContent()
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

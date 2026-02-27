package com.example.demo.controller;

import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.service.ConversationService;
import com.example.demo.dto.Message;
import com.example.demo.dto.OllamaChatRequest;
import com.example.demo.dto.OllamaChatResponse;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
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

    private final FastApiClient fastApiClient;
    private final ConversationService conversationService;

    public ChatController(FastApiClient fastApiClient, ConversationService conversationService) {
        this.fastApiClient = fastApiClient;
        this.conversationService = conversationService;
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

        RecommendResponse fastapi = null;
        try {
            fastapi = fastApiClient.recommend(
                    new RecommendRequest(enrichedQuery, 60, 3, conversation.getId())
            );
        } catch (Exception ex) {
            logger.warn("FastAPI recommend failed: {}", ex.getMessage());
        }

        String status = "success";
        boolean awaiting = false;
        List<String> pendingSubQuestions = List.of();
        String content = "";

        if (fastapi != null) {
            status = firstNonBlank(fastapi.getStatus(), "success");
            awaiting = Boolean.TRUE.equals(fastapi.getAwaitingHumanConfirmation());
            pendingSubQuestions = normalizeSubQuestions(fastapi.getPendingSubQuestions());
            content = firstNonBlank(fastapi.getFinalAnswer(), "");
        } else if (query != null && !query.isBlank()) {
            status = "error";
            content = "FastAPI service unavailable. Please try again.";
        }

        if (content != null && !content.isBlank()) {
            conversationService.appendMessage(conversation, "assistant", content);
        }

        String sessionId = firstNonBlank(
                fastapi == null ? null : fastapi.getSessionId(),
                conversation.getId()
        );
        logger.info(
                "chat response status={}, awaiting={}, pending_count={}, session_id={}",
                status,
                awaiting,
                pendingSubQuestions.size(),
                sessionId
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

    @PostMapping("/api/chat/confirm")
    public OllamaChatResponse confirm(@RequestBody Map<String, Object> request) {
        String requestSessionId = firstNonBlank(
                asText(request.get("session_id")),
                asText(request.get("conversation_id"))
        );
        if (requestSessionId == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "session_id is required");
        }

        ConversationEntity conversation = conversationService.ensureConversation(
                requestSessionId,
                "HITL confirmation",
                asText(request.get("model"))
        );

        String action = normalizeConfirmAction(asText(request.get("action")));
        List<String> subQuestions = normalizeSubQuestions(request.get("sub_questions"));
        if ("edit".equals(action) && subQuestions.isEmpty()) {
            action = "confirm";
        }
        String comment = firstNonBlank(asText(request.get("comment")), "confirm");

        RecommendResponse fastapi;
        try {
            fastapi = fastApiClient.confirm(conversation.getId(), action, subQuestions, comment);
        } catch (Exception ex) {
            logger.warn("FastAPI confirm failed: {}", ex.getMessage());
            fastapi = null;
        }

        String status = firstNonBlank(fastapi == null ? null : fastapi.getStatus(), "error");
        boolean awaiting = fastapi != null && Boolean.TRUE.equals(fastapi.getAwaitingHumanConfirmation());
        List<String> pendingSubQuestions = normalizeSubQuestions(fastapi == null ? null : fastapi.getPendingSubQuestions());
        String content = firstNonBlank(
                fastapi == null ? null : fastapi.getFinalAnswer(),
                fastapi == null ? "FastAPI confirm unavailable. Please try again." : ""
        );

        if (content != null && !content.isBlank()) {
            conversationService.appendMessage(conversation, "assistant", content);
        }

        String sessionId = firstNonBlank(
                fastapi == null ? null : fastapi.getSessionId(),
                conversation.getId()
        );
        logger.info(
                "chat confirm response status={}, awaiting={}, pending_count={}, session_id={}, action={}",
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
        if (raw == null || raw.isEmpty()) {
            return List.of();
        }
        List<String> cleaned = new ArrayList<>();
        for (String item : raw) {
            String text = firstNonBlank(item);
            if (text != null) {
                cleaned.add(text);
            }
        }
        return cleaned;
    }

    private List<String> normalizeSubQuestions(Object raw) {
        if (!(raw instanceof List<?> list) || list.isEmpty()) {
            return List.of();
        }
        List<String> cleaned = new ArrayList<>();
        for (Object item : list) {
            String text = firstNonBlank(asText(item));
            if (text != null) {
                cleaned.add(text);
            }
        }
        return cleaned;
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

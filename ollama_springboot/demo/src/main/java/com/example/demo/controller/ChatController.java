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
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

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

        String content = "";
        if (fastapi != null && fastapi.getFinalAnswer() != null) {
            content = fastapi.getFinalAnswer();
        } else if (fastapi == null && query != null && !query.isBlank()) {
            content = "FastAPI service unavailable. Please try again.";
        }

        if (content != null && !content.isBlank()) {
            conversationService.appendMessage(conversation, "assistant", content);
        }

        String sessionId = (fastapi != null && fastapi.getSessionId() != null && !fastapi.getSessionId().isBlank())
                ? fastapi.getSessionId()
                : conversation.getId();

        return buildResponse(request, content, conversation.getId(), sessionId);
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

    private String firstNonBlank(String first, String second) {
        if (first != null && !first.isBlank()) {
            return first;
        }
        if (second != null && !second.isBlank()) {
            return second;
        }
        return null;
    }

    private OllamaChatResponse buildResponse(
            OllamaChatRequest request,
            String content,
            String conversationId,
            String sessionId
    ) {
        Message message = new Message();
        message.setRole("assistant");
        message.setContent(content);

        OllamaChatResponse response = new OllamaChatResponse();
        response.setModel(request.getModel());
        response.setMessage(message);
        response.setDone(true);
        response.setConversationId(conversationId);
        response.setSessionId(sessionId);
        return response;
    }
}

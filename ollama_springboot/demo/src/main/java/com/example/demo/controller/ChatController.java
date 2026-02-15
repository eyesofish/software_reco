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
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
public class ChatController {
    private static final Logger logger = LoggerFactory.getLogger(ChatController.class);
    private static final Pattern NAME_ZH_PATTERN = Pattern.compile(
            "(?:(?:\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u7684\\u540d\\u5b57\\u662f|\\u6211\\u7684\\u540d\\u5b57\\u662f)\\s*([\\p{IsHan}A-Za-z][\\p{IsHan}A-Za-z0-9_\\-]{0,31}))"
    );
    private static final Pattern NAME_EN_PATTERN = Pattern.compile(
            "(?i)(?:my name is|i am|i'm)\\s+([A-Za-z][A-Za-z\\-' ]{0,40})"
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

        String query = extractLastUserMessage(request.getMessages());
        if (query == null || query.isBlank()) {
            return buildResponse(
                    request,
                    "Please enter a message.",
                    request.getConversationId(),
                    request.getConversationId()
            );
        }

        ConversationEntity conversation = conversationService.ensureConversation(
                request.getConversationId(),
                query,
                request.getModel()
        );

        ConversationMessageEntity userMessage = conversationService.appendMessage(conversation, "user", query);

        extractUserName(query).ifPresent(name ->
                conversationService.upsertFact(conversation.getId(), "user_name", name, userMessage.getId(), 0.99d)
        );

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

        Matcher zh = NAME_ZH_PATTERN.matcher(query);
        if (zh.find()) {
            return Optional.of(zh.group(1).trim());
        }

        Matcher en = NAME_EN_PATTERN.matcher(query);
        if (en.find()) {
            return Optional.of(en.group(1).trim());
        }

        return Optional.empty();
    }

    private boolean isAskingUserName(String query) {
        if (query == null) {
            return false;
        }
        String normalized = query.trim().toLowerCase(Locale.ROOT);
        return normalized.contains("\u6211\u53eb\u4ec0\u4e48")
                || normalized.contains("\u6211\u7684\u540d\u5b57")
                || normalized.contains("what is my name")
                || normalized.contains("who am i");
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

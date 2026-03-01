package com.example.demo.conversation.controller;

import com.example.demo.conversation.dto.SessionStateApiModels;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.service.ConversationService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestController
@RequestMapping("/api/session-state")
public class SessionStateController {
    private static final Pattern WAITING_ACK_PATTERN = Pattern.compile(
            "(?i)waiting\\s+for\\s+confirmation|\\u5f85\\u786e\\u8ba4|\\u7b49\\u5f85\\u786e\\u8ba4|\\u786e\\u8ba4\\u540e\\u7ee7\\u7eed"
    );
    private static final Pattern PENDING_LINE_PATTERN = Pattern.compile("^\\s*\\d+[\\.)\\u3001]\\s*(.+?)\\s*$");

    private final ConversationService conversationService;

    public SessionStateController(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @GetMapping("/{conversationId}")
    public SessionStateApiModels.Response get(@PathVariable String conversationId) {
        ConversationEntity conversation = conversationService.findActiveConversation(conversationId);
        return toResponse(conversation, conversationService.getFacts(conversationId));
    }

    @PutMapping("/{conversationId}")
    public SessionStateApiModels.Response upsert(
            @PathVariable String conversationId,
            @Valid @RequestBody SessionStateApiModels.UpsertRequest request
    ) {
        ConversationEntity conversation = conversationService.ensureConversation(conversationId, "New Conversation", null);
        Map<String, String> updates = new LinkedHashMap<>();
        if (request.facts() != null) {
            updates.putAll(request.facts());
        }
        if (request.userName() != null && !request.userName().isBlank()) {
            updates.put("user_name", request.userName().trim());
        }

        Map<String, String> merged = conversationService.upsertFacts(conversation.getId(), updates, null, 1.0d);
        ConversationEntity refreshed = conversationService.findActiveConversation(conversation.getId());
        return toResponse(refreshed, merged);
    }

    private SessionStateApiModels.Response toResponse(
            ConversationEntity conversation,
            Map<String, String> facts
    ) {
        List<ConversationMessageEntity> messageEntities =
                conversationService.getSessionMessages(conversation.getId(), 200);
        List<SessionStateApiModels.MessageItem> messages = messageEntities.stream()
                .map(message -> new SessionStateApiModels.MessageItem(message.getRole(), message.getContent()))
                .toList();
        HitlState hitlState = resolveHitlState(messages);

        return new SessionStateApiModels.Response(
                conversation.getId(),
                conversation.getId(),
                facts,
                messages,
                hitlState.awaiting(),
                hitlState.pendingSubQuestions(),
                conversation.getUpdatedAt()
        );
    }

    private HitlState resolveHitlState(List<SessionStateApiModels.MessageItem> messages) {
        for (int i = messages.size() - 1; i >= 0; i--) {
            SessionStateApiModels.MessageItem message = messages.get(i);
            if (!"assistant".equalsIgnoreCase(message.role())) {
                continue;
            }
            String content = message.content() == null ? "" : message.content().trim();
            if (content.isEmpty()) {
                continue;
            }
            List<String> pending = extractPendingSubQuestions(content);
            boolean awaiting = WAITING_ACK_PATTERN.matcher(content).find() || !pending.isEmpty();
            return new HitlState(awaiting, pending);
        }
        return new HitlState(false, List.of());
    }

    private List<String> extractPendingSubQuestions(String content) {
        List<String> pending = new ArrayList<>();
        for (String line : content.split("\\R")) {
            Matcher matcher = PENDING_LINE_PATTERN.matcher(line.trim());
            if (!matcher.find()) {
                continue;
            }
            String candidate = matcher.group(1).trim();
            if (!candidate.isEmpty()) {
                pending.add(candidate);
            }
        }
        return pending;
    }

    private record HitlState(
            boolean awaiting,
            List<String> pendingSubQuestions
    ) {
    }
}

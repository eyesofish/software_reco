package com.example.demo.conversation.controller;

import com.example.demo.conversation.dto.SessionStateApiModels;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.service.ConversationService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/session-state")
public class SessionStateController {
    private final ConversationService conversationService;

    public SessionStateController(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @GetMapping("/{conversationId}")
    public SessionStateApiModels.Response get(@PathVariable String conversationId) {
        ConversationEntity conversation = conversationService.findActiveConversation(conversationId);
        return new SessionStateApiModels.Response(
                conversation.getId(),
                conversation.getId(),
                conversationService.getFacts(conversationId),
                conversation.getUpdatedAt()
        );
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
        return new SessionStateApiModels.Response(
                refreshed.getId(),
                refreshed.getId(),
                merged,
                refreshed.getUpdatedAt()
        );
    }
}

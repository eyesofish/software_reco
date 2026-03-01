package com.example.demo.conversation.dto;

import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class SessionStateApiModels {
    private SessionStateApiModels() {
    }

    public record UpsertRequest(
            @Size(max = 100) String userName,
            Map<String, String> facts
    ) {
    }

    public record Response(
            String conversationId,
            String sessionId,
            Map<String, String> facts,
            List<MessageItem> messages,
            Boolean awaitingHumanConfirmation,
            List<String> pendingSubQuestions,
            Instant updatedAt
    ) {
    }

    public record MessageItem(
            String role,
            String content
    ) {
    }
}

package com.example.demo.conversation.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class ConversationApiModels {
    private ConversationApiModels() {
    }

    public record CreateRequest(
            @Size(max = 200) String title,
            @NotNull @Valid FirstMessage firstMessage,
            @Size(max = 128) String model
    ) {
        public record FirstMessage(
                @NotBlank @Size(max = 16) String role,
                @NotBlank @Size(max = 4000) String content
        ) {
        }
    }

    public record UpdateRequest(
            @Size(max = 200) String title,
            Boolean archived,
            Boolean pinned
    ) {
        public boolean hasAnyField() {
            return title != null || archived != null || pinned != null;
        }
    }

    public record ListItem(
            String id,
            String title,
            String preview,
            int messageCount,
            String lastMessageRole,
            Instant lastMessageAt,
            Instant updatedAt,
            Instant createdAt,
            boolean archived
    ) {
    }

    public record Pagination(
            int page,
            int pageSize,
            long total,
            int totalPages,
            boolean hasNext,
            boolean hasPrev
    ) {
    }

    public record ListResponse(List<ListItem> items, Pagination pagination) {
    }

    public record MessageItem(String id, String role, String content, Instant createdAt) {
    }

    public record CreateResponse(
            String id,
            String title,
            Instant createdAt,
            Instant updatedAt,
            int messageCount,
            List<MessageItem> messages
    ) {
    }

    public record UpdateResponse(
            String id,
            String title,
            boolean archived,
            boolean pinned,
            Instant updatedAt
    ) {
    }

    public record DeleteResponse(String id, boolean deleted, boolean hard, Instant deletedAt) {
    }
}

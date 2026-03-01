package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

public class ConversationCreateResponse {
    @JsonProperty("conversation_id")
    private String conversationId;
    @JsonProperty("created_at")
    private Instant createdAt;

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
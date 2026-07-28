package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public class RecommendTaskCreateRequest {
    private String query;
    @JsonProperty("conversation_id")
    private String conversationId;
    @JsonProperty("session_id")
    private String sessionId;
    private Integer timeout;
    @JsonProperty("max_iterations")
    private Integer maxIterations;
    private List<ImageAttachment> images;

    public String getQuery() {
        return query;
    }

    public void setQuery(String query) {
        this.query = query;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public Integer getTimeout() {
        return timeout;
    }

    public void setTimeout(Integer timeout) {
        this.timeout = timeout;
    }

    public Integer getMaxIterations() {
        return maxIterations;
    }

    public void setMaxIterations(Integer maxIterations) {
        this.maxIterations = maxIterations;
    }

    public List<ImageAttachment> getImages() {
        return images;
    }

    public void setImages(List<ImageAttachment> images) {
        this.images = images;
    }
}
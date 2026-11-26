package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public class OllamaChatRequest {
    @JsonProperty("routing_mode")
    private String routingMode;
    @JsonProperty("current_conversation_id")
    private String currentConversationId;
    @JsonProperty("thread_id")
    private String threadId;
    public String getRoutingMode() { return routingMode; }
    public void setRoutingMode(String value) { routingMode = value; }
    public String getCurrentConversationId() { return currentConversationId; }
    public void setCurrentConversationId(String value) { currentConversationId = value; }
    public String getThreadId() { return threadId; }
    public void setThreadId(String value) { threadId = value; }

    private String model;
    private List<Message> messages;
    private List<ImageAttachment> images;
    private Boolean stream;
    @JsonProperty("conversation_id")
    private String conversationId;
    @JsonProperty("session_id")
    private String sessionId;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public List<Message> getMessages() { return messages; }
    public void setMessages(List<Message> messages) { this.messages = messages; }
    public List<ImageAttachment> getImages() { return images; }
    public void setImages(List<ImageAttachment> images) { this.images = images; }
    public Boolean getStream() { return stream; }
    public void setStream(Boolean stream) { this.stream = stream; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
}

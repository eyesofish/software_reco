package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class OllamaChatResponse {
    private String model;
    private Message message;
    private boolean done;
    @JsonProperty("conversation_id")
    private String conversationId;
    @JsonProperty("session_id")
    private String sessionId;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public Message getMessage() { return message; }
    public void setMessage(Message message) { this.message = message; }
    public boolean isDone() { return done; }
    public void setDone(boolean done) { this.done = done; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
}

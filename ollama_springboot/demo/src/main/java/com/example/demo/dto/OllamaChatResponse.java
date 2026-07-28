package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

public class OllamaChatResponse {
    private String model;
    private Message message;
    private boolean done;
    private String status;
    @JsonProperty("awaiting_human_confirmation")
    private Boolean awaitingHumanConfirmation;
    @JsonProperty("pending_sub_questions")
    private List<String> pendingSubQuestions;
    @JsonProperty("conversation_id")
    private String conversationId;
    @JsonProperty("session_id")
    private String sessionId;
    @JsonProperty("retrieved_images")
    private List<Map<String, Object>> retrievedImages;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public Message getMessage() { return message; }
    public void setMessage(Message message) { this.message = message; }
    public boolean isDone() { return done; }
    public void setDone(boolean done) { this.done = done; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public Boolean getAwaitingHumanConfirmation() { return awaitingHumanConfirmation; }
    public void setAwaitingHumanConfirmation(Boolean awaitingHumanConfirmation) { this.awaitingHumanConfirmation = awaitingHumanConfirmation; }
    public List<String> getPendingSubQuestions() { return pendingSubQuestions; }
    public void setPendingSubQuestions(List<String> pendingSubQuestions) { this.pendingSubQuestions = pendingSubQuestions; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public List<Map<String, Object>> getRetrievedImages() { return retrievedImages; }
    public void setRetrievedImages(List<Map<String, Object>> retrievedImages) { this.retrievedImages = retrievedImages; }
}

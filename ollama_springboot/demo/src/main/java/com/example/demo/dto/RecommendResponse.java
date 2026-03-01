package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class RecommendResponse {
    private String status;
    @JsonProperty("final_answer")
    private String finalAnswer;
    @JsonProperty("awaiting_human_confirmation")
    private Boolean awaitingHumanConfirmation;
    @JsonProperty("pending_sub_questions")
    private Object pendingSubQuestions;
    @JsonProperty("session_id")
    private String sessionId;

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getFinalAnswer() { return finalAnswer; }
    public void setFinalAnswer(String finalAnswer) { this.finalAnswer = finalAnswer; }
    public Boolean getAwaitingHumanConfirmation() { return awaitingHumanConfirmation; }
    public void setAwaitingHumanConfirmation(Boolean awaitingHumanConfirmation) { this.awaitingHumanConfirmation = awaitingHumanConfirmation; }
    public Object getPendingSubQuestions() { return pendingSubQuestions; }
    public void setPendingSubQuestions(Object pendingSubQuestions) { this.pendingSubQuestions = pendingSubQuestions; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
}

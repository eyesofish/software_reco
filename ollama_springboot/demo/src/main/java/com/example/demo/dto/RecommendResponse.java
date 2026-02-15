package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class RecommendResponse {
    private String status;
    @JsonProperty("final_answer")
    private String finalAnswer;
    @JsonProperty("session_id")
    private String sessionId;

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getFinalAnswer() { return finalAnswer; }
    public void setFinalAnswer(String finalAnswer) { this.finalAnswer = finalAnswer; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
}

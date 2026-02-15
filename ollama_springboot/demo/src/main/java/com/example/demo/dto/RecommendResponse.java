package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class RecommendResponse {
    private String status;
    @JsonProperty("final_answer")
    private String finalAnswer;

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getFinalAnswer() { return finalAnswer; }
    public void setFinalAnswer(String finalAnswer) { this.finalAnswer = finalAnswer; }
}
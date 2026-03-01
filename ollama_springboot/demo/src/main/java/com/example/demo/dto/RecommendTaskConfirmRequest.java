package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public class RecommendTaskConfirmRequest {
    @JsonProperty("sub_questions")
    private List<String> subQuestions;
    private String comment;

    public List<String> getSubQuestions() {
        return subQuestions;
    }

    public void setSubQuestions(List<String> subQuestions) {
        this.subQuestions = subQuestions;
    }

    public String getComment() {
        return comment;
    }

    public void setComment(String comment) {
        this.comment = comment;
    }
}
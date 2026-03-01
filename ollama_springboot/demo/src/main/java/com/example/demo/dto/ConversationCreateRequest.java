package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class ConversationCreateRequest {
    private String title;
    @JsonProperty("model_name")
    private String modelName;

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getModelName() {
        return modelName;
    }

    public void setModelName(String modelName) {
        this.modelName = modelName;
    }
}
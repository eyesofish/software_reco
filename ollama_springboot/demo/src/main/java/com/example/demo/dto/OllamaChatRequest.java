package com.example.demo.dto;

import java.util.List;

public class OllamaChatRequest {
    private String model;
    private List<Message> messages;
    private Boolean stream;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public List<Message> getMessages() { return messages; }
    public void setMessages(List<Message> messages) { this.messages = messages; }
    public Boolean getStream() { return stream; }
    public void setStream(Boolean stream) { this.stream = stream; }
}
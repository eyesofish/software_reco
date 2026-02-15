package com.example.demo.dto;

public class OllamaChatResponse {
    private String model;
    private Message message;
    private boolean done;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public Message getMessage() { return message; }
    public void setMessage(Message message) { this.message = message; }
    public boolean isDone() { return done; }
    public void setDone(boolean done) { this.done = done; }
}
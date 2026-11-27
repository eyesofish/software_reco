package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

public class RecommendRequest {
    private boolean routingPrechecked;
    public boolean isRoutingPrechecked() { return routingPrechecked; }
    @JsonProperty("routing_decision")
    private Map<String, Object> routingDecision;
    @JsonProperty("execution_budget_seconds")
    private Double executionBudgetSeconds;
    public Map<String, Object> getRoutingDecision() { return routingDecision; }
    public void setRoutingDecision(Map<String, Object> value) { routingDecision = value; }
    public Double getExecutionBudgetSeconds() { return executionBudgetSeconds; }
    public void setExecutionBudgetSeconds(Double value) { executionBudgetSeconds = value; }
    public RecommendRequest routed(Map<String, Object> decision, double remaining) {
        routingPrechecked = true; routingDecision = decision; executionBudgetSeconds = remaining; return this;
    }

    private String query;
    private Integer timeout;
    @JsonProperty("max_iterations")
    private Integer maxIterations;
    @JsonProperty("session_id")
    private String sessionId;
    private List<ImageAttachment> images;

    public RecommendRequest() {
    }

    public RecommendRequest(String query, Integer timeout, Integer maxIterations, String sessionId) {
        this(query, timeout, maxIterations, sessionId, List.of());
    }

    public RecommendRequest(
            String query,
            Integer timeout,
            Integer maxIterations,
            String sessionId,
            List<ImageAttachment> images
    ) {
        this.query = query;
        this.timeout = timeout;
        this.maxIterations = maxIterations;
        this.sessionId = sessionId;
        this.images = images;
    }

    public String getQuery() {
        return query;
    }

    public void setQuery(String query) {
        this.query = query;
    }

    public Integer getTimeout() {
        return timeout;
    }

    public void setTimeout(Integer timeout) {
        this.timeout = timeout;
    }

    public Integer getMaxIterations() {
        return maxIterations;
    }

    public void setMaxIterations(Integer maxIterations) {
        this.maxIterations = maxIterations;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public List<ImageAttachment> getImages() {
        return images;
    }

    public void setImages(List<ImageAttachment> images) {
        this.images = images;
    }
}

package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class RecommendRequest {
    private String query;
    private Integer timeout;
    @JsonProperty("max_iterations")
    private Integer max_iterations;

    public RecommendRequest() {
    }

    public RecommendRequest(String query, Integer timeout, Integer maxIterations) {
        this.query = query;
        this.timeout = timeout;
        this.max_iterations = maxIterations;
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
        return max_iterations;
    }

    public void setMaxIterations(Integer maxIterations) {
        this.max_iterations = maxIterations;
    }
}

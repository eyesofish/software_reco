package com.example.demo.controller;

import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.RecommendTaskStateResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/recommend/task")
public class RecommendTaskController {
    private final RecommendTaskStateService recommendTaskStateService;

    public RecommendTaskController(RecommendTaskStateService recommendTaskStateService) {
        this.recommendTaskStateService = recommendTaskStateService;
    }

    @GetMapping("/{taskId}")
    public RecommendTaskStateResponse getTaskState(@PathVariable String taskId) {
        return recommendTaskStateService.getTaskState(taskId);
    }
}

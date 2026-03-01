package com.example.demo.controller;

import com.example.demo.conversation.service.RecommendTaskStateService;
import com.example.demo.dto.ConversationCreateRequest;
import com.example.demo.dto.ConversationCreateResponse;
import com.example.demo.dto.RecommendTaskConfirmRequest;
import com.example.demo.dto.RecommendTaskCreateRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@RestController
@RequestMapping("/api/v1")
public class RecommendTaskController {
    private static final Logger logger = LoggerFactory.getLogger(RecommendTaskController.class);

    private final RecommendTaskStateService recommendTaskStateService;

    public RecommendTaskController(RecommendTaskStateService recommendTaskStateService) {
        this.recommendTaskStateService = recommendTaskStateService;
    }

    @PostMapping("/conversations")
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationCreateResponse createConversation(
            @RequestBody(required = false) ConversationCreateRequest request
    ) {
        String title = request == null ? null : request.getTitle();
        String modelName = request == null ? null : request.getModelName();
        ConversationCreateResponse response = recommendTaskStateService.createConversation(title, modelName);
        logger.info("v1 conversation created conversation_id={}", response.getConversationId());
        return response;
    }

    @PostMapping("/recommend")
    public RecommendTaskStateResponse recommend(@RequestBody RecommendTaskCreateRequest request) {
        if (request == null || request.getQuery() == null || request.getQuery().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "query is required");
        }

        String conversationId = firstNonBlank(request.getConversationId(), request.getSessionId());

        RecommendTaskStateService.RecommendTaskCreateOutcome outcome = recommendTaskStateService.createRecommendTask(
                conversationId,
                request.getQuery(),
                request.getTimeout(),
                request.getMaxIterations(),
                null
        );

        RecommendTaskStateResponse response = outcome.taskState();
        logger.info(
                "v1 recommend task created task_id={}, conversation_id={}, status={}, pending_count={}",
                response.getTaskId(),
                response.getConversationId(),
                response.getStatus(),
                response.getSubQuestions() == null ? 0 : response.getSubQuestions().size()
        );
        return response;
    }

    @PostMapping("/recommend/confirm")
    public RecommendTaskStateResponse confirm(
            @RequestParam("taskId") String taskId,
            @RequestParam(name = "action", defaultValue = "confirm") String action,
            @RequestBody(required = false) RecommendTaskConfirmRequest request
    ) {
        List<String> subQuestions = request == null ? List.of() : request.getSubQuestions();
        String comment = request == null ? null : request.getComment();

        logger.info(
                "v1 confirm request received task_id={}, action={}, pending_count={}",
                taskId,
                action,
                subQuestions == null ? 0 : subQuestions.size()
        );

        RecommendTaskStateResponse response = recommendTaskStateService.confirmTask(taskId, action, subQuestions, comment);

        logger.info(
                "v1 confirm accepted task_id={}, status={}, conversation_id={}",
                response.getTaskId(),
                response.getStatus(),
                response.getConversationId()
        );
        return response;
    }

    @GetMapping("/tasks/{taskId}")
    public RecommendTaskStateResponse getTaskState(@PathVariable String taskId) {
        return recommendTaskStateService.getTaskState(taskId);
    }

    @GetMapping("/conversations/{conversationId}/tasks")
    public List<RecommendTaskStateResponse> listConversationTasks(@PathVariable String conversationId) {
        return recommendTaskStateService.listConversationTasks(conversationId);
    }

    @GetMapping("/recommend/task/{taskId}")
    public RecommendTaskStateResponse getTaskStateCompatibility(@PathVariable String taskId) {
        return recommendTaskStateService.getTaskState(taskId);
    }

    private String firstNonBlank(String... values) {
        if (values == null || values.length == 0) {
            return null;
        }
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }
}
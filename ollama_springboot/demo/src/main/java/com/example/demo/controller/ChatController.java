package com.example.demo.controller;

import com.example.demo.client.FastApiClient;
import com.example.demo.dto.Message;
import com.example.demo.dto.OllamaChatRequest;
import com.example.demo.dto.OllamaChatResponse;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class ChatController {
    private static final Logger logger = LoggerFactory.getLogger(ChatController.class);
    private final FastApiClient fastApiClient;

    public ChatController(FastApiClient fastApiClient) {
        this.fastApiClient = fastApiClient;
    }

    @PostMapping("/api/chat")
    public OllamaChatResponse chat(@RequestBody OllamaChatRequest request) {
        if (request.getMessages() == null) {
            logger.info("Chat request messages is null. model={}, stream={}", request.getModel(), request.getStream());
        } else {
            for (int i = 0; i < request.getMessages().size(); i++) {
                Message message = request.getMessages().get(i);
                logger.info(
                        "Chat message[{}]: role={}, content={}",
                        i,
                        message.getRole(),
                        message.getContent()
                );
            }
        }

        String query = extractLastUserMessage(request.getMessages());
        if (query == null || query.isBlank()) {
            return buildResponse(request, "Please enter a message.");
        }

        RecommendResponse fastapi = null;
        try {
            fastapi = fastApiClient.recommend(new RecommendRequest(query, 60, 3));
        } catch (Exception ex) {
            logger.warn("FastAPI recommend failed: {}", ex.getMessage());
        }

        String content = "";
        if (fastapi != null && fastapi.getFinalAnswer() != null) {
            content = fastapi.getFinalAnswer();
        } else if (fastapi == null && query != null && !query.isBlank()) {
            content = "FastAPI service unavailable. Please try again.";
        }
        return buildResponse(request, content);
    }

    private String extractLastUserMessage(List<Message> messages) {
        if (messages == null || messages.isEmpty()) {
            return "";
        }
        for (int i = messages.size() - 1; i >= 0; i--) {
            if ("user".equalsIgnoreCase(messages.get(i).getRole())) {
                return messages.get(i).getContent();
            }
        }
        return messages.get(messages.size() - 1).getContent();
    }

    private OllamaChatResponse buildResponse(OllamaChatRequest request, String content) {
        Message message = new Message();
        message.setRole("assistant");
        message.setContent(content);

        OllamaChatResponse response = new OllamaChatResponse();
        response.setModel(request.getModel());
        response.setMessage(message);
        response.setDone(true);
        return response;
    }
}

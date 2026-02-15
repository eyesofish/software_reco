package com.example.demo.conversation.controller;

import com.example.demo.conversation.dto.ConversationApiModels;
import com.example.demo.conversation.service.ConversationService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.data.domain.Sort;
import org.springframework.http.HttpStatus;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/conversations")
public class ConversationController {
    private final ConversationService conversationService;

    public ConversationController(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @GetMapping
    public ConversationApiModels.ListResponse list(
            @RequestParam(defaultValue = "1") @Min(1) int page,
            @RequestParam(name = "page_size", defaultValue = "20") @Min(1) @Max(100) int pageSize,
            @RequestParam(required = false) String q,
            @RequestParam(name = "sort", defaultValue = "updated_at:desc") String sort,
            @RequestParam(name = "include_archived", defaultValue = "false") boolean includeArchived
    ) {
        return conversationService.list(page, pageSize, q, includeArchived, parseSort(sort));
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationApiModels.CreateResponse create(
            @Valid @RequestBody ConversationApiModels.CreateRequest request
    ) {
        return conversationService.create(request);
    }

    @PatchMapping("/{id}")
    public ConversationApiModels.UpdateResponse patch(
            @PathVariable String id,
            @Valid @RequestBody ConversationApiModels.UpdateRequest request
    ) {
        return conversationService.patch(id, request);
    }

    @DeleteMapping("/{id}")
    public ConversationApiModels.DeleteResponse delete(
            @PathVariable String id,
            @RequestParam(defaultValue = "false") boolean hard
    ) {
        return conversationService.delete(id, hard);
    }

    private Sort parseSort(String sort) {
        String[] parts = (sort == null ? "updated_at:desc" : sort).split(":", 2);
        String field = switch (parts[0]) {
            case "created_at" -> "createdAt";
            case "last_message_at" -> "lastMessageAt";
            default -> "updatedAt";
        };
        Sort.Direction direction = (parts.length > 1 && "asc".equalsIgnoreCase(parts[1]))
                ? Sort.Direction.ASC
                : Sort.Direction.DESC;
        return Sort.by(direction, field);
    }
}

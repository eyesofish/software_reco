package com.example.demo.auth;

import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.RecommendTaskEntity;
import com.example.demo.conversation.repository.ConversationRepository;
import com.example.demo.conversation.repository.RecommendTaskRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** Authorizes external identifiers before lifecycle mutations, persistence or Agent calls. */
@Service
@Transactional(readOnly = true)
public class BusinessAccess {
    private static final Logger logger = LoggerFactory.getLogger(BusinessAccess.class);
    private final ConversationRepository conversations;
    private final RecommendTaskRepository tasks;
    public BusinessAccess(ConversationRepository conversations, RecommendTaskRepository tasks) {
        this.conversations = conversations; this.tasks = tasks;
    }
    public ConversationEntity conversation(String owner, String id) {
        ConversationEntity conversation = conversations.findById(id).orElseThrow(() -> denied(owner, id));
        return check(owner, conversation);
    }
    public RecommendTaskEntity task(String owner, String id) {
        RecommendTaskEntity task = tasks.findById(id).orElseThrow(() -> denied(owner, id));
        check(owner, task.getConversation());
        return task;
    }
    public String referenceConversation(String owner, String id) {
        if (id == null || id.isBlank()) return null;
        var conversation = conversations.findById(id);
        if (conversation.isPresent()) return check(owner, conversation.get()).getId();
        var task = tasks.findById(id).or(() -> tasks.findTopByFastapiSessionIdOrderByCreatedAtDesc(id));
        if (task.isEmpty()) throw denied(owner, id);
        return check(owner, task.get().getConversation()).getId();
    }
    public String matchingConversation(String owner, String... identifiers) {
        String resolved = null;
        for (String id : identifiers) {
            String candidate = referenceConversation(owner, id);
            if (candidate == null) continue;
            if (resolved != null && !resolved.equals(candidate)) throw denied(owner, id);
            resolved = candidate;
        }
        return resolved;
    }
    private ConversationEntity check(String owner, ConversationEntity conversation) {
        if (conversation.isDeleted() || owner == null || !owner.equals(conversation.getOwnerUserId()))
            throw denied(owner, conversation.getId());
        return conversation;
    }
    private ResponseStatusException denied(String owner, String id) {
        logger.info("business.access.denied user_id={} resource_id={}", owner, id);
        return new ResponseStatusException(HttpStatus.NOT_FOUND, "Resource not found");
    }
}

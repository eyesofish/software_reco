package com.example.demo.conversation.service;

import com.example.demo.conversation.dto.ConversationApiModels;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationFactEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.repository.ConversationFactRepository;
import com.example.demo.conversation.repository.ConversationMessageRepository;
import com.example.demo.conversation.repository.ConversationRepository;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Service
public class ConversationService {
    private final ConversationRepository conversationRepository;
    private final ConversationMessageRepository messageRepository;
    private final ConversationFactRepository factRepository;

    public ConversationService(
            ConversationRepository conversationRepository,
            ConversationMessageRepository messageRepository,
            ConversationFactRepository factRepository
    ) {
        this.conversationRepository = conversationRepository;
        this.messageRepository = messageRepository;
        this.factRepository = factRepository;
    }

    @Transactional(readOnly = true)
    public ConversationApiModels.ListResponse list(
            int page,
            int pageSize,
            String q,
            boolean includeArchived,
            Sort sort
    ) {
        Pageable pageable = PageRequest.of(Math.max(page - 1, 0), Math.max(1, Math.min(pageSize, 100)), sort);
        Specification<ConversationEntity> spec = (root, query, cb) -> {
            List<Predicate> predicates = new ArrayList<>();
            predicates.add(cb.isFalse(root.get("deleted")));
            if (!includeArchived) {
                predicates.add(cb.isFalse(root.get("archived")));
            }
            if (q != null && !q.isBlank()) {
                String like = "%" + q.trim().toLowerCase(Locale.ROOT) + "%";
                predicates.add(
                        cb.or(
                                cb.like(cb.lower(root.get("title")), like),
                                cb.like(cb.lower(root.get("preview")), like)
                        )
                );
            }
            return cb.and(predicates.toArray(new Predicate[0]));
        };

        Page<ConversationEntity> pageResult = conversationRepository.findAll(spec, pageable);
        List<ConversationApiModels.ListItem> items = pageResult.getContent().stream()
                .map(c -> new ConversationApiModels.ListItem(
                        c.getId(),
                        c.getTitle(),
                        c.getPreview(),
                        c.getMessageCount(),
                        c.getLastMessageRole(),
                        c.getLastMessageAt(),
                        c.getUpdatedAt(),
                        c.getCreatedAt(),
                        c.isArchived()
                ))
                .toList();

        return new ConversationApiModels.ListResponse(
                items,
                new ConversationApiModels.Pagination(
                        pageResult.getNumber() + 1,
                        pageResult.getSize(),
                        pageResult.getTotalElements(),
                        pageResult.getTotalPages(),
                        pageResult.hasNext(),
                        pageResult.hasPrevious()
                )
        );
    }

    @Transactional
    public ConversationApiModels.CreateResponse create(ConversationApiModels.CreateRequest request) {
        String role = normalizeRole(request.firstMessage().role());
        if (!"user".equals(role)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "first_message.role must be user");
        }
        String content = request.firstMessage().content().trim();

        ConversationEntity conversation = new ConversationEntity();
        conversation.setTitle(resolveTitle(request.title(), content));
        conversation.setPreview(content.length() > 500 ? content.substring(0, 500) : content);
        conversation.setModelName(trimToNull(request.model()));
        conversation = conversationRepository.save(conversation);

        ConversationMessageEntity message = new ConversationMessageEntity();
        message.setConversation(conversation);
        message.setRole(role);
        message.setContent(content);
        message = messageRepository.save(message);

        conversation.setMessageCount(1);
        conversation.setLastMessageRole(message.getRole());
        conversation.setLastMessageAt(message.getCreatedAt());
        conversation.setUpdatedAt(message.getCreatedAt());
        conversation = conversationRepository.save(conversation);

        return new ConversationApiModels.CreateResponse(
                conversation.getId(),
                conversation.getTitle(),
                conversation.getCreatedAt(),
                conversation.getUpdatedAt(),
                conversation.getMessageCount(),
                List.of(
                        new ConversationApiModels.MessageItem(
                                message.getId(),
                                message.getRole(),
                                message.getContent(),
                                message.getCreatedAt()
                        )
                )
        );
    }

    @Transactional
    public ConversationApiModels.UpdateResponse patch(String id, ConversationApiModels.UpdateRequest request) {
        if (!request.hasAnyField()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least one field is required");
        }

        ConversationEntity conversation = findActiveConversation(id);

        if (request.title() != null) {
            conversation.setTitle(resolveTitle(request.title(), conversation.getPreview()));
        }
        if (request.archived() != null) {
            conversation.setArchived(request.archived());
        }
        if (request.pinned() != null) {
            conversation.setPinned(request.pinned());
        }

        conversation = conversationRepository.save(conversation);
        return new ConversationApiModels.UpdateResponse(
                conversation.getId(),
                conversation.getTitle(),
                conversation.isArchived(),
                conversation.isPinned(),
                conversation.getUpdatedAt()
        );
    }

    @Transactional
    public ConversationApiModels.DeleteResponse delete(String id, boolean hard) {
        ConversationEntity conversation = findActiveConversation(id);

        Instant deletedAt = Instant.now();
        if (hard) {
            conversationRepository.delete(conversation);
        } else {
            conversation.setDeleted(true);
            conversation.setArchived(true);
            conversation.setUpdatedAt(deletedAt);
            conversationRepository.save(conversation);
        }
        return new ConversationApiModels.DeleteResponse(id, true, hard, deletedAt);
    }

    @Transactional
    public ConversationEntity ensureConversation(String id, String titleSeed, String modelName) {
        String normalizedId = trimToNull(id);
        if (normalizedId != null) {
            return conversationRepository.findByIdAndDeletedFalse(normalizedId)
                    .orElseGet(() -> {
                        ConversationEntity created = new ConversationEntity();
                        created.setId(normalizedId);
                        created.setTitle(resolveTitle(titleSeed, titleSeed));
                        created.setModelName(trimToNull(modelName));
                        return conversationRepository.save(created);
                    });
        }

        ConversationEntity created = new ConversationEntity();
        created.setTitle(resolveTitle(titleSeed, titleSeed));
        created.setModelName(trimToNull(modelName));
        return conversationRepository.save(created);
    }

    @Transactional(readOnly = true)
    public ConversationEntity findActiveConversation(String id) {
        return conversationRepository.findByIdAndDeletedFalse(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Conversation not found"));
    }

    @Transactional
    public ConversationMessageEntity appendMessage(String conversationId, String role, String content) {
        return appendMessage(findActiveConversation(conversationId), role, content);
    }

    @Transactional
    public ConversationMessageEntity appendMessage(ConversationEntity conversation, String role, String content) {
        String normalizedContent = trimToNull(content);
        if (normalizedContent == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "message content is required");
        }

        ConversationMessageEntity message = new ConversationMessageEntity();
        message.setConversation(conversation);
        message.setRole(normalizeRole(role));
        message.setContent(normalizedContent);
        message = messageRepository.save(message);

        conversation.setMessageCount(conversation.getMessageCount() + 1);
        conversation.setLastMessageRole(message.getRole());
        conversation.setLastMessageAt(message.getCreatedAt());
        if ("user".equals(message.getRole())) {
            conversation.setPreview(normalizedContent.length() > 500 ? normalizedContent.substring(0, 500) : normalizedContent);
        }
        conversation.setUpdatedAt(message.getCreatedAt());
        conversationRepository.save(conversation);

        return message;
    }

    @Transactional(readOnly = true)
    public List<ConversationMessageEntity> getRecentMessages(String conversationId, int limit) {
        int size = Math.max(1, Math.min(limit, 50));
        return messageRepository.findByConversation_IdOrderByCreatedAtDesc(conversationId, PageRequest.of(0, size)).getContent();
    }

    @Transactional(readOnly = true)
    public List<ConversationMessageEntity> getSessionMessages(String conversationId, int limit) {
        int size = Math.max(1, Math.min(limit, 200));
        List<ConversationMessageEntity> messages =
                messageRepository.findByConversation_IdOrderByCreatedAtDesc(conversationId, PageRequest.of(0, size)).getContent();
        Collections.reverse(messages);
        return messages;
    }

    @Transactional(readOnly = true)
    public Map<String, String> getFacts(String conversationId) {
        List<ConversationFactEntity> facts = factRepository.findByConversation_IdOrderByUpdatedAtDesc(conversationId);
        Map<String, String> out = new LinkedHashMap<>();
        for (ConversationFactEntity fact : facts) {
            out.putIfAbsent(fact.getFactKey(), fact.getFactValue());
        }
        return out;
    }

    @Transactional
    public Map<String, String> upsertFacts(String conversationId, Map<String, String> updates, String sourceMessageId, double confidence) {
        if (updates == null || updates.isEmpty()) {
            return getFacts(conversationId);
        }
        for (Map.Entry<String, String> entry : updates.entrySet()) {
            upsertFact(conversationId, entry.getKey(), entry.getValue(), sourceMessageId, confidence);
        }
        return getFacts(conversationId);
    }

    @Transactional
    public void upsertFact(String conversationId, String key, String value, String sourceMessageId, double confidence) {
        String factKey = trimToNull(key);
        String factValue = trimToNull(value);
        if (factKey == null || factValue == null) {
            return;
        }

        ConversationEntity conversation = findActiveConversation(conversationId);
        ConversationFactEntity fact = factRepository.findByConversation_IdAndFactKey(conversationId, factKey)
                .orElseGet(() -> {
                    ConversationFactEntity created = new ConversationFactEntity();
                    created.setConversation(conversation);
                    created.setFactKey(factKey);
                    return created;
                });

        fact.setFactValue(factValue);
        fact.setSourceMessageId(trimToNull(sourceMessageId));
        fact.setConfidence(Math.max(0.0d, Math.min(confidence, 1.0d)));
        factRepository.save(fact);

        conversation.setUpdatedAt(Instant.now());
        conversationRepository.save(conversation);
    }

    private String normalizeRole(String role) {
        String value = trimToNull(role);
        if (value == null) {
            return "user";
        }
        value = value.toLowerCase(Locale.ROOT);
        if (!"user".equals(value) && !"assistant".equals(value) && !"system".equals(value)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid role");
        }
        return value;
    }

    private String resolveTitle(String title, String fallback) {
        String resolved = trimToNull(title);
        if (resolved == null) {
            resolved = trimToNull(fallback);
        }
        if (resolved == null) {
            resolved = "New Conversation";
        }
        return resolved.length() > 200 ? resolved.substring(0, 200) : resolved;
    }

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }
}

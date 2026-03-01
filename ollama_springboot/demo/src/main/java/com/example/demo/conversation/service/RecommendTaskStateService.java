package com.example.demo.conversation.service;

import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.dto.RecommendTaskStateResponse;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class RecommendTaskStateService {
    private static final String FACT_TASK_STATUS = "rag_task_status";
    private static final String FACT_TASK_PENDING_SUB_QUESTIONS = "rag_task_pending_sub_questions";
    private static final int FACT_MAX_VALUE_LENGTH = 480;
    private static final Pattern WAITING_ACK_PATTERN = Pattern.compile(
            "(?i)waiting\\s+for\\s+confirmation|\\u5f85\\u786e\\u8ba4|\\u7b49\\u5f85\\u786e\\u8ba4|\\u786e\\u8ba4\\u540e\\u7ee7\\u7eed"
    );
    private static final Pattern PENDING_LINE_PATTERN = Pattern.compile("^\\s*\\d+[\\.)\\u3001]\\s*(.+?)\\s*$");

    private final ConversationService conversationService;

    public RecommendTaskStateService(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @Transactional(readOnly = true)
    public RecommendTaskStateResponse getTaskState(String taskId) {
        ConversationEntity conversation = conversationService.findActiveConversation(taskId);
        Map<String, String> facts = conversationService.getFacts(taskId);
        List<ConversationMessageEntity> messages = conversationService.getSessionMessages(taskId, 200);

        HitlState hitlState = resolveHitlState(messages);
        List<String> storedSubQuestions = parsePendingSubQuestions(
                facts.getOrDefault(FACT_TASK_PENDING_SUB_QUESTIONS, "")
        );
        List<String> subQuestions = !storedSubQuestions.isEmpty()
                ? storedSubQuestions
                : hitlState.pendingSubQuestions();

        String finalResult = resolveFinalResult(messages);
        String status = resolveReadStatus(
                facts.get(FACT_TASK_STATUS),
                hitlState.awaiting(),
                finalResult,
                hasRole(messages, "assistant"),
                hasRole(messages, "user")
        );

        RecommendTaskStateResponse response = new RecommendTaskStateResponse();
        response.setTaskId(conversation.getId());
        response.setStatus(status);
        response.setSubQuestions(subQuestions);
        response.setFinalResult(finalResult);
        response.setCreatedAt(conversation.getCreatedAt());
        response.setUpdatedAt(conversation.getUpdatedAt());
        return response;
    }

    @Transactional
    public void markGenerating(String taskId) {
        upsertTaskFacts(taskId, "GENERATING", List.of());
    }

    @Transactional
    public void markConfirmed(String taskId, List<String> subQuestions) {
        upsertTaskFacts(taskId, "CONFIRMED", normalizeSubQuestions(subQuestions));
    }

    @Transactional
    public void markFromResponse(
            String taskId,
            String rawStatus,
            boolean awaitingHumanConfirmation,
            List<String> pendingSubQuestions,
            String finalResult
    ) {
        List<String> normalizedSubQuestions = normalizeSubQuestions(pendingSubQuestions);
        boolean hasFinalResult = finalResult != null && !finalResult.isBlank();
        String status = resolveWriteStatus(rawStatus, awaitingHumanConfirmation, hasFinalResult);
        upsertTaskFacts(taskId, status, normalizedSubQuestions);
    }

    private void upsertTaskFacts(String taskId, String status, List<String> subQuestions) {
        Map<String, String> updates = new LinkedHashMap<>();
        updates.put(FACT_TASK_STATUS, trimFactValue(status));
        updates.put(FACT_TASK_PENDING_SUB_QUESTIONS, trimFactValue(String.join("\n", subQuestions)));
        conversationService.upsertFacts(taskId, updates, null, 1.0d);
    }

    private String resolveWriteStatus(String rawStatus, boolean awaiting, boolean hasFinalResult) {
        if (awaiting) {
            return "PENDING_CONFIRM";
        }

        String normalized = normalizeStatus(rawStatus);
        if ("FAILED".equals(normalized)) {
            return "FAILED";
        }
        if ("PENDING_CONFIRM".equals(normalized)) {
            return "PENDING_CONFIRM";
        }
        if ("CONFIRMED".equals(normalized) || "GENERATING".equals(normalized)) {
            return normalized;
        }
        if (hasFinalResult) {
            return "DONE";
        }
        if ("SUCCESS".equalsIgnoreCase(rawStatus)) {
            return "DONE";
        }
        return "GENERATING";
    }

    private String resolveReadStatus(
            String rawStatus,
            boolean awaiting,
            String finalResult,
            boolean hasAssistantMessage,
            boolean hasUserMessage
    ) {
        String normalized = normalizeStatus(rawStatus);
        if (normalized != null) {
            if ("PENDING_CONFIRM".equals(normalized) && awaiting) {
                return normalized;
            }
            if ("PENDING_CONFIRM".equals(normalized) && !awaiting && finalResult != null && !finalResult.isBlank()) {
                return "DONE";
            }
            if (("CONFIRMED".equals(normalized) || "GENERATING".equals(normalized))
                    && finalResult != null && !finalResult.isBlank()) {
                return "DONE";
            }
            return normalized;
        }

        if (awaiting) {
            return "PENDING_CONFIRM";
        }
        if (finalResult != null && !finalResult.isBlank()) {
            return "DONE";
        }
        if (hasUserMessage && !hasAssistantMessage) {
            return "GENERATING";
        }
        if (hasAssistantMessage) {
            return "DONE";
        }
        return "FAILED";
    }

    private String normalizeStatus(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        String normalized = status.trim().toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "PENDING_CONFIRM", "CONFIRMED", "GENERATING", "DONE", "FAILED" -> normalized;
            case "SUCCESS" -> "DONE";
            case "ERROR" -> "FAILED";
            case "AWAITING_HUMAN_CONFIRMATION" -> "PENDING_CONFIRM";
            default -> null;
        };
    }

    private String resolveFinalResult(List<ConversationMessageEntity> messages) {
        for (int i = messages.size() - 1; i >= 0; i--) {
            ConversationMessageEntity message = messages.get(i);
            if (!"assistant".equalsIgnoreCase(message.getRole())) {
                continue;
            }
            String content = trimToNull(message.getContent());
            if (content == null) {
                continue;
            }
            if (WAITING_ACK_PATTERN.matcher(content).find()) {
                continue;
            }
            return content;
        }
        return "";
    }

    private HitlState resolveHitlState(List<ConversationMessageEntity> messages) {
        for (int i = messages.size() - 1; i >= 0; i--) {
            ConversationMessageEntity message = messages.get(i);
            if (!"assistant".equalsIgnoreCase(message.getRole())) {
                continue;
            }
            String content = trimToNull(message.getContent());
            if (content == null) {
                continue;
            }
            List<String> pending = extractPendingSubQuestions(content);
            boolean awaiting = WAITING_ACK_PATTERN.matcher(content).find() || !pending.isEmpty();
            if (awaiting) {
                return new HitlState(true, pending);
            }
        }
        return new HitlState(false, List.of());
    }

    private List<String> extractPendingSubQuestions(String content) {
        List<String> pending = new ArrayList<>();
        for (String line : content.split("\\R")) {
            Matcher matcher = PENDING_LINE_PATTERN.matcher(line.trim());
            if (!matcher.find()) {
                continue;
            }
            String candidate = trimToNull(matcher.group(1));
            if (candidate != null) {
                pending.add(candidate);
            }
        }
        return normalizeSubQuestions(pending);
    }

    private List<String> parsePendingSubQuestions(String raw) {
        String normalized = trimToNull(raw);
        if (normalized == null) {
            return List.of();
        }
        List<String> pending = new ArrayList<>();
        for (String line : normalized.split("\\R")) {
            String candidate = trimToNull(line);
            if (candidate != null) {
                pending.add(candidate);
            }
        }
        return normalizeSubQuestions(pending);
    }

    private List<String> normalizeSubQuestions(List<String> raw) {
        if (raw == null || raw.isEmpty()) {
            return List.of();
        }
        Set<String> unique = new LinkedHashSet<>();
        for (String item : raw) {
            String text = trimToNull(item);
            if (text != null) {
                unique.add(text);
            }
        }
        return new ArrayList<>(unique);
    }

    private String trimFactValue(String value) {
        String normalized = trimToNull(value);
        if (normalized == null) {
            return "none";
        }
        return normalized.length() > FACT_MAX_VALUE_LENGTH
                ? normalized.substring(0, FACT_MAX_VALUE_LENGTH)
                : normalized;
    }

    private boolean hasRole(List<ConversationMessageEntity> messages, String role) {
        for (ConversationMessageEntity message : messages) {
            if (role.equalsIgnoreCase(message.getRole())) {
                return true;
            }
        }
        return false;
    }

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim();
        return normalized.isEmpty() ? null : normalized;
    }

    private record HitlState(
            boolean awaiting,
            List<String> pendingSubQuestions
    ) {
    }
}

package com.example.demo.conversation.service;

import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.entity.RecommendTaskEntity;
import com.example.demo.conversation.entity.RecommendTaskStatus;
import com.example.demo.conversation.repository.RecommendTaskRepository;
import com.example.demo.dto.ConversationCreateResponse;
import com.example.demo.dto.ImageAttachment;
import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import com.example.demo.dto.RecommendTaskStateResponse;
import com.example.demo.util.MultimodalSupport;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.Instant;
import java.lang.reflect.Array;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class RecommendTaskStateService {
    private static final Logger logger = LoggerFactory.getLogger(RecommendTaskStateService.class);
    private static final Pattern QUOTED_STRING_PATTERN = Pattern.compile("'((?:\\\\.|[^'])*)'|\"((?:\\\\.|[^\"])*)\"");
    private static final int DEFAULT_TIMEOUT_SECONDS = 60;
    private static final int DEFAULT_MAX_ITERATIONS = 3;
    private static final Duration TASK_EXPIRE_AFTER = Duration.ofHours(24);
    private static final Duration STALLED_THRESHOLD = Duration.ofSeconds(60);
    private static final int HEARTBEAT_INTERVAL_SECONDS = 4;
    private static final List<RecommendTaskStatus> ACTIVE_STATUSES = List.of(RecommendTaskStatus.GENERATING,
            RecommendTaskStatus.CONFIRMED, RecommendTaskStatus.PENDING_CONFIRM, RecommendTaskStatus.PENDING_INPUT);

    private final ConversationService conversationService;
    private final RecommendTaskRepository recommendTaskRepository;
    private final FastApiClient fastApiClient;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactionTemplate;

    private final ExecutorService confirmExecutor = Executors.newCachedThreadPool();
    private final ScheduledExecutorService heartbeatExecutor = Executors.newScheduledThreadPool(2);
    private final Set<String> runningTaskIds = ConcurrentHashMap.newKeySet();
    private final Map<String, ScheduledFuture<?>> heartbeatFutures = new ConcurrentHashMap<>();

    public RecommendTaskStateService(
            ConversationService conversationService,
            RecommendTaskRepository recommendTaskRepository,
            FastApiClient fastApiClient,
            ObjectMapper objectMapper,
            PlatformTransactionManager transactionManager
    ) {
        this.conversationService = conversationService;
        this.recommendTaskRepository = recommendTaskRepository;
        this.fastApiClient = fastApiClient;
        this.objectMapper = objectMapper;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    @Transactional
    public ConversationCreateResponse createConversation(String owner, String title, String modelName) {
        ConversationEntity conversation = conversationService.ensureOwnedConversation(
                owner, null,
                firstNonBlank(title, "New Conversation"),
                modelName
        );
        ConversationCreateResponse response = new ConversationCreateResponse();
        response.setConversationId(conversation.getId());
        response.setCreatedAt(conversation.getCreatedAt());
        return response;
    }

    @Transactional
    public RecommendTaskCreateOutcome createRecommendTask(
            String owner,
            String conversationId,
            String query,
            Integer timeout,
            Integer maxIterations,
            String modelName
    ) {
        return createRecommendTask(
                owner,
                conversationId,
                query,
                timeout,
                maxIterations,
                modelName,
                List.of()
        );
    }

    @Transactional
    public RecommendTaskCreateOutcome createRecommendTask(
            String owner,
            String conversationId,
            String query,
            Integer timeout,
            Integer maxIterations,
            String modelName,
            List<ImageAttachment> images
    ) {
        return createRecommendTask(owner, conversationId, query, timeout, maxIterations, modelName,
                images, null, timeout == null ? DEFAULT_TIMEOUT_SECONDS : timeout);
    }

    @Transactional
    public RecommendTaskCreateOutcome createRecommendTask(String owner, String conversationId, String query,
            Integer timeout, Integer maxIterations, String modelName, List<ImageAttachment> images,
            Map<String, Object> routingDecision, double executionBudgetSeconds) {
        String normalizedQuery = trimToNull(query);
        if (normalizedQuery == null) {
            throw new IllegalArgumentException("query is required");
        }

        if (conversationId != null) assertConversationAvailable(owner, conversationId);
        ConversationEntity conversation = conversationService.ensureOwnedConversation(owner, conversationId, normalizedQuery, modelName);

        RecommendResponse fastapiResponse = null;
        String errorMessage = null;

        try {
            fastapiResponse = fastApiClient.recommend(
                    new RecommendRequest(
                            normalizedQuery,
                            timeout == null ? DEFAULT_TIMEOUT_SECONDS : timeout,
                            maxIterations == null ? DEFAULT_MAX_ITERATIONS : maxIterations,
                            conversation.getId(),
                            images
                    ).routed(routingDecision, executionBudgetSeconds)
            );
        } catch (Exception ex) {
            errorMessage = toErrorMessage(ex);
            logger.error(
                    "recommend forward failed conversation_id={}, error={}",
                    conversation.getId(),
                    errorMessage,
                    ex
            );
        }

        List<String> subQuestions = normalizeSubQuestions(
                fastapiResponse == null ? List.of() : fastapiResponse.getPendingSubQuestions()
        );
        String assistantMessage = fastapiResponse == null
                ? ""
                : firstNonBlank(fastapiResponse.getFinalAnswer(), "");
        String fastapiSessionId = firstNonBlank(
                fastapiResponse == null ? null : fastapiResponse.getSessionId(),
                conversation.getId()
        );
        boolean awaiting = Boolean.TRUE.equals(
                fastapiResponse == null ? null : fastapiResponse.getAwaitingHumanConfirmation()
        );

        RecommendTaskStatus status = resolveStatusFromFastApi(
                fastapiResponse == null ? "FAILED" : fastapiResponse.getStatus(),
                awaiting,
                subQuestions,
                errorMessage
        );

        RecommendTaskEntity task = new RecommendTaskEntity();
        task.setConversation(conversation);
        task.setFastapiSessionId(fastapiSessionId);
        applyCanonicalTaskState(task, status, subQuestions, assistantMessage, errorMessage);
        if (status == RecommendTaskStatus.PENDING_INPUT && fastapiResponse != null)
            task.setPendingInput(serializeMap(fastapiResponse.getPendingInput()));
        task.setProgress(progressForStatus(status));
        task.setLastHeartbeatAt(Instant.now());
        task.setExpireAt(Instant.now().plus(TASK_EXPIRE_AFTER));

        RecommendTaskEntity savedTask = recommendTaskRepository.save(task);
        if ((savedTask.getStatus() == RecommendTaskStatus.DONE || savedTask.getStatus() == RecommendTaskStatus.INCOMPLETE) && trimToNull(assistantMessage) != null) {
            appendAssistantIfNeeded(conversation.getId(), assistantMessage);
        }

        return new RecommendTaskCreateOutcome(
                toResponse(savedTask),
                firstNonBlank(assistantMessage, ""),
                fastapiSessionId,
                awaiting,
                subQuestions,
                fastapiResponse == null
                        ? List.of()
                        : MultimodalSupport.normalizeRetrievedImages(fastapiResponse.getRetrievedImages())
        );
    }

    @Transactional
    public RecommendTaskStateResponse createStreamingTask(
            String owner,
            String conversationId,
            String query,
            Integer timeout,
            Integer maxIterations,
            String modelName
    ) {
        String normalizedQuery = trimToNull(query);
        if (normalizedQuery == null) {
            throw new IllegalArgumentException("query is required");
        }

        ConversationEntity conversation = conversationId == null
                ? conversationService.ensureOwnedConversation(owner, null, normalizedQuery, modelName)
                : assertConversationAvailable(owner, conversationId);
        RecommendTaskEntity task = new RecommendTaskEntity();
        task.setConversation(conversation);
        task.setFastapiSessionId(conversation.getId());
        applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
        task.setProgress(progressForStatus(RecommendTaskStatus.GENERATING));
        task.setLastHeartbeatAt(Instant.now());
        task.setExpireAt(Instant.now().plus(TASK_EXPIRE_AFTER));

        return toResponse(recommendTaskRepository.save(task));
    }

    @Transactional(rollbackFor = Exception.class)
    public RecommendTaskStateResponse applyStreamEvent(
            String taskId,
            String eventType,
            Map<String, Object> payload
    ) {
        String normalizedTaskId = trimToNull(taskId);
        if (normalizedTaskId == null) {
            return expiredResponse("", "missing task id");
        }

        String normalizedEventType = firstNonBlank(eventType, asText(payload == null ? null : payload.get("type")), "state")
                .toLowerCase(Locale.ROOT);
        // All writers that need both rows lock the conversation before its task.
        ConversationEntity conversation = "final".equals(normalizedEventType)
                ? conversationService.lockTaskConversation(normalizedTaskId).orElse(null) : null;
        RecommendTaskEntity task = recommendTaskRepository.lockById(normalizedTaskId)
                .orElse(null);
        if (task == null) {
            return expiredResponse(normalizedTaskId, "task not found");
        }

        task = applyLifecycleRules(task, Instant.now());
        if (isTerminal(task.getStatus()) || task.getStatus() == RecommendTaskStatus.PENDING_INPUT
                || task.getStatus() == RecommendTaskStatus.PENDING_CONFIRM) {
            return toResponse(task);
        }
        Instant now = Instant.now();

        switch (normalizedEventType) {
            case "meta" -> {
                String sessionId = firstNonBlank(
                        asText(payload == null ? null : payload.get("session_id")),
                        asText(payload == null ? null : payload.get("conversation_id")),
                        task.getFastapiSessionId()
                );
                task.setFastapiSessionId(sessionId);
                applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
                task.setProgress(Math.max(progressForStatus(RecommendTaskStatus.GENERATING),
                        task.getProgress() == null ? 0 : task.getProgress()));
                task.setLastHeartbeatAt(now);
            }
            case "node", "state", "token", "route_decision" -> {
                applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
                int progress = task.getProgress() == null ? 0 : task.getProgress();
                task.setProgress(Math.min(Math.max(progress, progressForStatus(RecommendTaskStatus.GENERATING)) + 1, 95));
                task.setLastHeartbeatAt(now);
            }
            case "awaiting_input" -> {
                applyCanonicalTaskState(task, RecommendTaskStatus.PENDING_INPUT, List.of(), null, null);
                Object pending = payload == null ? null : payload.get("pending_input");
                if (!(pending instanceof Map)) throw new IllegalArgumentException("awaiting_input requires pending_input");
                task.setPendingInput(serializeMap((Map<?, ?>) pending));
                task.setLastHeartbeatAt(now);
            }
            case "awaiting_confirmation" -> {
                List<String> pending = normalizeSubQuestions(payload == null ? null : payload.get("sub_questions"));
                applyCanonicalTaskState(task, RecommendTaskStatus.PENDING_CONFIRM, pending, null, null);
                task.setProgress(progressForStatus(RecommendTaskStatus.PENDING_CONFIRM));
                task.setLastHeartbeatAt(now);
            }
            case "final" -> {
                if (conversation == null || conversation.isDeleted()) {
                    applyCanonicalTaskState(task, RecommendTaskStatus.FAILED, List.of(), null, "Conversation was deleted");
                    task.setProgress(progressForStatus(RecommendTaskStatus.FAILED));
                    task.setLastHeartbeatAt(now);
                    break;
                }
                String finalAnswer = firstNonBlank(
                        asText(payload == null ? null : payload.get("final_answer")),
                        asText(payload == null ? null : payload.get("message")),
                        ""
                );
                String reported = firstNonBlank(asText(payload == null ? null : payload.get("status")), "success");
                if (finalAnswer == null && ("success".equalsIgnoreCase(reported) || "done".equalsIgnoreCase(reported))) {
                    applyCanonicalTaskState(task, RecommendTaskStatus.FAILED, List.of(), null, "Agent returned an empty answer");
                    task.setProgress(progressForStatus(RecommendTaskStatus.FAILED));
                    task.setLastHeartbeatAt(now);
                    break;
                }
                RecommendTaskStatus finalStatus = switch (reported.toLowerCase(Locale.ROOT)) {
                    case "done", "success" -> RecommendTaskStatus.DONE;
                    case "incomplete" -> RecommendTaskStatus.INCOMPLETE;
                    case "cancelled" -> RecommendTaskStatus.CANCELLED;
                    case "expired" -> RecommendTaskStatus.EXPIRED;
                    default -> RecommendTaskStatus.FAILED;
                };
                applyCanonicalTaskState(task, finalStatus, List.of(), finalAnswer,
                        isFailureStatus(finalStatus) ? "Agent could not complete" : null);
                if (finalStatus == RecommendTaskStatus.DONE || finalStatus == RecommendTaskStatus.INCOMPLETE) {
                    task.setResultMetadata(serializeResultMetadata(payload));
                }
                task.setProgress(progressForStatus(finalStatus));
                task.setLastHeartbeatAt(now);
                if ((finalStatus == RecommendTaskStatus.DONE || finalStatus == RecommendTaskStatus.INCOMPLETE)
                        && trimToNull(finalAnswer) != null) {
                    conversationService.appendMessage(conversation, "assistant", finalAnswer);
                }
            }
            case "error" -> {
                String errorMessage = firstNonBlank(
                        asText(payload == null ? null : payload.get("message")),
                        "stream failed"
                );
                applyCanonicalTaskState(task, RecommendTaskStatus.FAILED, List.of(), null, errorMessage);
                task.setProgress(progressForStatus(RecommendTaskStatus.FAILED));
                task.setLastHeartbeatAt(now);
            }
            default -> task.setLastHeartbeatAt(now);
        }

        RecommendTaskEntity saved = recommendTaskRepository.save(task);
        return toResponse(saved);
    }

    @Transactional
    public RecommendTaskStateResponse confirmTask(
            String taskId,
            String action,
            List<String> subQuestions,
            String comment
    ) {
        String normalizedTaskId = trimToNull(taskId);
        if (normalizedTaskId == null) {
            return expiredResponse("", "missing task id");
        }

        RecommendTaskEntity task = recommendTaskRepository.lockById(normalizedTaskId)
                .orElse(null);
        if (task == null) {
            return expiredResponse(normalizedTaskId, "task not found");
        }

        task = applyLifecycleRules(task, Instant.now());
        if (isTerminal(task.getStatus()) || task.getStatus() == RecommendTaskStatus.EXPIRED) {
            return toResponse(task);
        }

        if (task.getStatus() != RecommendTaskStatus.PENDING_CONFIRM)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Task is not awaiting confirmation");
        String normalizedAction = normalizeAction(action);
        List<String> normalizedSubQuestions = normalizeSubQuestions(subQuestions);
        if (normalizedSubQuestions.isEmpty()) {
            normalizedSubQuestions = deserializeSubQuestions(task.getSubQuestions());
        }

        applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
        task.setProgress(progressForStatus(RecommendTaskStatus.GENERATING));
        task.setLastHeartbeatAt(Instant.now());

        RecommendTaskEntity savedTask = recommendTaskRepository.save(task);

        startAsyncConfirm(
                savedTask.getId(),
                savedTask.getFastapiSessionId(),
                normalizedAction,
                normalizedSubQuestions,
                firstNonBlank(comment, "confirm")
        );

        return toResponse(savedTask);
    }

    @Transactional
    public ConfirmStreamPreparation prepareConfirmStreamingTask(
            String taskId,
            String action,
            List<String> subQuestions,
            String comment
    ) {
        String normalizedTaskId = trimToNull(taskId);
        if (normalizedTaskId == null) {
            throw new IllegalArgumentException("missing task id");
        }

        RecommendTaskEntity task = recommendTaskRepository.lockById(normalizedTaskId)
                .orElse(null);
        if (task == null) {
            throw new IllegalArgumentException("task not found");
        }

        task = applyLifecycleRules(task, Instant.now());
        if (isTerminal(task.getStatus()) || task.getStatus() == RecommendTaskStatus.EXPIRED) {
            throw new IllegalStateException("task is not confirmable");
        }
        if (task.getStatus() != RecommendTaskStatus.PENDING_CONFIRM)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Task is not awaiting confirmation");
        String normalizedAction = normalizeAction(action);
        List<String> normalizedSubQuestions = normalizeSubQuestions(subQuestions);
        if (normalizedSubQuestions.isEmpty()) {
            normalizedSubQuestions = deserializeSubQuestions(task.getSubQuestions());
        }

        applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
        task.setProgress(progressForStatus(RecommendTaskStatus.GENERATING));
        task.setLastHeartbeatAt(Instant.now());

        RecommendTaskEntity savedTask = recommendTaskRepository.save(task);
        RecommendTaskStateResponse taskState = toResponse(savedTask);

        return new ConfirmStreamPreparation(
                taskState,
                firstNonBlank(
                        savedTask.getFastapiSessionId(),
                        savedTask.getConversation() == null ? null : savedTask.getConversation().getId()
                ),
                normalizedAction,
                normalizedSubQuestions,
                firstNonBlank(comment, "confirm")
        );
    }

    @Transactional
    public ConversationEntity assertConversationAvailable(String owner, String conversationId) {
        ConversationEntity conversation = conversationService.lockOwnedConversation(owner, conversationId);
        Instant now = Instant.now();
        for (RecommendTaskEntity task : recommendTaskRepository.findByConversation_IdAndStatusIn(conversationId, ACTIVE_STATUSES)) {
            if (!isTerminal(applyLifecycleRules(task, now).getStatus())) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "Conversation has an active or waiting task; resume or cancel it first");
            }
        }
        return conversation;
    }

    @Transactional
    public ClarifyPreparation prepareClarification(String taskId, String clarificationId, String answer) {
        if (trimToNull(answer) == null || answer.length() > 4000 || trimToNull(clarificationId) == null)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "clarification_id and an answer of at most 4000 characters are required");
        RecommendTaskEntity task = recommendTaskRepository.lockById(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Task not found"));
        task = applyLifecycleRules(task, Instant.now());
        Map<String, Object> pending = deserializeMap(task.getPendingInput());
        if (task.getStatus() != RecommendTaskStatus.PENDING_INPUT
                || !clarificationId.equals(pending.get("clarification_id")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Clarification is stale or task is not waiting for input");
        String reservationId = UUID.randomUUID().toString();
        Map<String, Object> reserved = new LinkedHashMap<>(pending);
        reserved.put("_resume_reservation_id", reservationId);
        applyCanonicalTaskState(task, RecommendTaskStatus.GENERATING, List.of(), null, null);
        // Keep the original interruption until Python has accepted the resume.
        // A reservation token prevents delayed failures from reverting a later retry.
        task.setPendingInput(serializeMap(reserved));
        task.setLastHeartbeatAt(Instant.now());
        recommendTaskRepository.save(task);
        return new ClarifyPreparation(toResponse(task), task.getFastapiSessionId(), clarificationId, answer.trim(), reservationId);
    }

    @Transactional
    public boolean acceptClarification(ClarifyPreparation preparation) {
        ConversationEntity conversation = conversationService.lockTaskConversation(preparation.taskState().getTaskId()).orElse(null);
        if (conversation == null || conversation.isDeleted()) return false;
        RecommendTaskEntity task = recommendTaskRepository.lockById(preparation.taskState().getTaskId()).orElseThrow();
        Map<String, Object> pending = deserializeMap(task.getPendingInput());
        if (task.getStatus() != RecommendTaskStatus.GENERATING
                || !preparation.reservationId().equals(pending.get("_resume_reservation_id"))) return false;
        conversationService.appendMessage(conversation, "user", preparation.answer());
        task.setPendingInput(null);
        task.setLastHeartbeatAt(Instant.now());
        recommendTaskRepository.save(task);
        return true;
    }

    @Transactional
    public boolean restoreClarification(ClarifyPreparation preparation) {
        RecommendTaskEntity task = recommendTaskRepository.lockById(preparation.taskState().getTaskId()).orElseThrow();
        Map<String, Object> pending = new LinkedHashMap<>(deserializeMap(task.getPendingInput()));
        if (task.getStatus() != RecommendTaskStatus.GENERATING
                || !preparation.reservationId().equals(pending.get("_resume_reservation_id"))) return false;
        pending.remove("_resume_reservation_id");
        applyCanonicalTaskState(task, RecommendTaskStatus.PENDING_INPUT, List.of(), null, null);
        task.setPendingInput(serializeMap(pending));
        task.setLastHeartbeatAt(Instant.now());
        recommendTaskRepository.save(task);
        return true;
    }

    public RecommendTaskStateResponse clarifyTask(String taskId, String clarificationId, String answer) {
        ClarifyPreparation preparation = transactionTemplate.execute(status -> prepareClarification(taskId, clarificationId, answer));
        RecommendResponse response;
        try {
            response = fastApiClient.clarify(preparation.sessionId(), clarificationId, preparation.answer());
            if (response == null) throw new IllegalStateException("Empty clarification response");
        } catch (Exception error) {
            transactionTemplate.executeWithoutResult(status -> restoreClarification(preparation));
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "Clarification could not start; the task is still waiting for input", error);
        }
        return transactionTemplate.execute(status -> {
            if (!acceptClarification(preparation))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "Clarification reservation is stale");
            applyConfirmResponse(taskId, response);
            return toResponse(recommendTaskRepository.findById(taskId).orElseThrow());
        });
    }

    @Transactional
    public RecommendTaskStateResponse cancelWaitingTask(String taskId) {
        RecommendTaskEntity task = recommendTaskRepository.lockById(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Task not found"));
        if (task.getStatus() == RecommendTaskStatus.CANCELLED) return toResponse(task);
        if (task.getStatus() != RecommendTaskStatus.PENDING_INPUT && task.getStatus() != RecommendTaskStatus.PENDING_CONFIRM)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Only a waiting task can be cancelled here");
        String clarificationId = asText(deserializeMap(task.getPendingInput()).get("clarification_id"));
        fastApiClient.cancelWaiting(task.getFastapiSessionId(), clarificationId);
        applyCanonicalTaskState(task, RecommendTaskStatus.CANCELLED, List.of(), null, null);
        task.setLastHeartbeatAt(Instant.now());
        cancelHeartbeat(taskId);
        return toResponse(recommendTaskRepository.save(task));
    }

    private String serializeResultMetadata(Map<String, Object> payload) {
        if (payload == null) return null;
        Map<String, Object> metadata = new LinkedHashMap<>();
        for (String key : List.of("retrieved_doc_ids", "retrieved_images", "run_id", "elapsed_ms", "stop_reason")) {
            if (payload.get(key) != null) metadata.put(key, payload.get(key));
        }
        return metadata.isEmpty() ? null : serializeMap(metadata);
    }

    private String serializeMap(Map<?, ?> value) {
        if (value == null) return null;
        try { return objectMapper.writeValueAsString(value); }
        catch (Exception error) { throw new IllegalArgumentException("Invalid pending input", error); }
    }

    private Map<String, Object> deserializeMap(String value) {
        if (value == null || value.isBlank()) return Map.of();
        try { return objectMapper.readValue(value, new TypeReference<Map<String, Object>>() {}); }
        catch (Exception error) { return Map.of(); }
    }

    public record ClarifyPreparation(RecommendTaskStateResponse taskState, String sessionId,
            String clarificationId, String answer, String reservationId) { }

    @Transactional
    public RecommendTaskStateResponse getOwnedTaskState(String owner, String taskId) {
        // The first task read in this transaction must take the write lock. Preloading it for
        // authorization would let Hibernate reuse an older snapshot when checking its heartbeat.
        RecommendTaskEntity task = recommendTaskRepository.lockOwnedById(owner, taskId).orElseThrow(() -> {
            logger.info("business.access.denied user_id={} resource_id={}", owner, taskId);
            return new ResponseStatusException(HttpStatus.NOT_FOUND, "Resource not found");
        });
        return toResponse(applyLifecycleRules(task, Instant.now()));
    }

    @Transactional
    public RecommendTaskStateResponse getTaskState(String taskId) {
        String normalizedTaskId = trimToNull(taskId);
        if (normalizedTaskId == null) {
            return expiredResponse("", "missing task id");
        }

        RecommendTaskEntity task = recommendTaskRepository.lockById(normalizedTaskId)
                .orElse(null);
        if (task == null) {
            return expiredResponse(normalizedTaskId, "task not found");
        }

        task = applyLifecycleRules(task, Instant.now());
        return toResponse(task);
    }

    @Transactional
    public List<RecommendTaskStateResponse> listConversationTasks(String conversationId) {
        String normalizedConversationId = trimToNull(conversationId);
        if (normalizedConversationId == null) {
            return List.of();
        }

        Instant now = Instant.now();
        for (RecommendTaskEntity task : recommendTaskRepository.findByConversation_IdAndStatusIn(normalizedConversationId, ACTIVE_STATUSES)) {
            applyLifecycleRules(task, now);
        }
        List<RecommendTaskEntity> tasks = recommendTaskRepository.findByConversation_IdOrderByCreatedAtDesc(normalizedConversationId);
        if (tasks.isEmpty()) {
            return List.of();
        }

        List<RecommendTaskStateResponse> responses = new ArrayList<>(tasks.size());
        for (RecommendTaskEntity task : tasks) {
            responses.add(toResponse(task));
        }
        return responses;
    }

    @Transactional(readOnly = true)
    public String resolveTaskId(String taskOrSessionOrConversationId) {
        String normalized = trimToNull(taskOrSessionOrConversationId);
        if (normalized == null) {
            return null;
        }

        if (recommendTaskRepository.existsById(normalized)) {
            return normalized;
        }

        Optional<RecommendTaskEntity> byConversation = recommendTaskRepository
                .findTopByConversation_IdOrderByCreatedAtDesc(normalized);
        if (byConversation.isPresent()) {
            return byConversation.get().getId();
        }

        Optional<RecommendTaskEntity> bySession = recommendTaskRepository
                .findTopByFastapiSessionIdOrderByCreatedAtDesc(normalized);
        return bySession.map(RecommendTaskEntity::getId).orElse(null);
    }

    @Scheduled(fixedDelay = 10_000)
    @Transactional
    public void reconcileStalledTasks() {
        Instant now = Instant.now();
        List<RecommendTaskEntity> candidates = recommendTaskRepository.lockStalledCandidates(
                List.of(RecommendTaskStatus.CONFIRMED, RecommendTaskStatus.GENERATING), now.minus(STALLED_THRESHOLD), now);
        for (RecommendTaskEntity task : candidates) {
            RecommendTaskEntity updated = applyLifecycleRules(task, now);
            if (updated.getStatus() == RecommendTaskStatus.FAILED) {
                cancelHeartbeat(updated.getId());
                runningTaskIds.remove(updated.getId());
            }
        }
    }

    private void startAsyncConfirm(
            String taskId,
            String fastapiSessionId,
            String action,
            List<String> subQuestions,
            String comment
    ) {
        if (!runningTaskIds.add(taskId)) {
            logger.info("confirm async already running task_id={}", taskId);
            return;
        }

        scheduleHeartbeat(taskId);

        confirmExecutor.submit(() -> {
            try {
                logger.info(
                        "calling FastApiClient.confirm task_id={}, session_id={}, action={}, pending_count={}",
                        taskId,
                        fastapiSessionId,
                        action,
                        subQuestions.size()
                );
                RecommendResponse response = fastApiClient.confirm(fastapiSessionId, action, subQuestions, comment);
                transactionTemplate.executeWithoutResult(status -> applyConfirmResponse(taskId, response));
            } catch (Exception ex) {
                String error = toErrorMessage(ex);
                logger.error(
                        "confirm async failed task_id={}, session_id={}, error={}",
                        taskId,
                        fastapiSessionId,
                        error,
                        ex
                );
                transactionTemplate.executeWithoutResult(status -> markFailed(taskId, error));
            } finally {
                cancelHeartbeat(taskId);
                runningTaskIds.remove(taskId);
            }
        });
    }

    private void applyConfirmResponse(String taskId, RecommendResponse response) {
        ConversationEntity conversation = conversationService.lockTaskConversation(taskId).orElse(null);
        RecommendTaskEntity task = recommendTaskRepository.lockById(taskId).orElse(null);
        if (task == null || isTerminal(task.getStatus())) {
            return;
        }
        if (conversation == null || conversation.isDeleted()) {
            markFailed(taskId, "Conversation was deleted");
            return;
        }

        List<String> pending = normalizeSubQuestions(response == null ? List.of() : response.getPendingSubQuestions());
        String finalResult = response == null ? null : trimToNull(response.getFinalAnswer());
        boolean awaiting = Boolean.TRUE.equals(response == null ? null : response.getAwaitingHumanConfirmation());
        String normalizedStatus = response == null ? "FAILED" : response.getStatus();

        RecommendTaskStatus nextStatus = resolveStatusFromFastApi(
                normalizedStatus,
                awaiting,
                pending,
                null
        );

        String nextError = nextStatus == RecommendTaskStatus.FAILED
                ? firstNonBlank(trimToNull(task.getErrorMessage()), "confirm failed")
                : null;
        applyCanonicalTaskState(task, nextStatus, pending, finalResult, nextError);
        if (nextStatus == RecommendTaskStatus.PENDING_INPUT && response != null)
            task.setPendingInput(serializeMap(response.getPendingInput()));
        task.setProgress(progressForStatus(nextStatus));
        task.setLastHeartbeatAt(Instant.now());

        RecommendTaskEntity saved = recommendTaskRepository.save(task);

        String persistedFinalResult = trimToNull(saved.getFinalResult());
        if ((saved.getStatus() == RecommendTaskStatus.DONE || saved.getStatus() == RecommendTaskStatus.INCOMPLETE) && persistedFinalResult != null) {
            appendAssistantIfNeeded(saved.getConversation().getId(), persistedFinalResult);
        }

        logger.info(
                "confirm async completed task_id={}, status={}, pending_count={}",
                saved.getId(),
                saved.getStatus(),
                pending.size()
        );
    }

    private void markFailed(String taskId, String error) {
        RecommendTaskEntity task = recommendTaskRepository.lockById(taskId).orElse(null);
        if (task == null || isTerminal(task.getStatus())) {
            return;
        }
        applyCanonicalTaskState(
                task,
                RecommendTaskStatus.FAILED,
                List.of(),
                null,
                firstNonBlank(trimToNull(error), "confirm failed")
        );
        task.setProgress(progressForStatus(RecommendTaskStatus.FAILED));
        task.setLastHeartbeatAt(Instant.now());
        recommendTaskRepository.save(task);
    }

    private RecommendTaskEntity applyLifecycleRules(RecommendTaskEntity task, Instant now) {
        boolean changed = false;

        Instant expireAt = task.getExpireAt();
        if (expireAt != null
                && expireAt.isBefore(now)
                && !isTerminal(task.getStatus())) {
            applyCanonicalTaskState(
                    task,
                    RecommendTaskStatus.EXPIRED,
                    List.of(),
                    null,
                    firstNonBlank(trimToNull(task.getErrorMessage()), "task expired")
            );
            task.setProgress(progressForStatus(RecommendTaskStatus.EXPIRED));
            changed = true;
        }

        if (task.getStatus() == RecommendTaskStatus.CONFIRMED || task.getStatus() == RecommendTaskStatus.GENERATING) {
            Instant heartbeat = task.getLastHeartbeatAt();
            Instant cutoff = now.minus(STALLED_THRESHOLD);
            if (heartbeat == null || heartbeat.isBefore(cutoff)) {
                applyCanonicalTaskState(task, RecommendTaskStatus.FAILED, List.of(), null, "stalled");
                task.setProgress(progressForStatus(RecommendTaskStatus.FAILED));
                changed = true;
            }
        }

        if (!changed) {
            return task;
        }

        String taskId = task.getId();
        String conversationId = task.getConversation().getId();
        RecommendTaskStatus status = task.getStatus();
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() {
                logger.warn("chat.committed conversation_id={} task_id={} stage=lifecycle status={}", conversationId, taskId, status);
            }
        });
        return recommendTaskRepository.save(task);
    }

    private void scheduleHeartbeat(String taskId) {
        cancelHeartbeat(taskId);

        ScheduledFuture<?> future = heartbeatExecutor.scheduleAtFixedRate(
                () -> transactionTemplate.executeWithoutResult(status -> {
                    RecommendTaskEntity task = recommendTaskRepository.lockById(taskId).orElse(null);
                    if (task == null) {
                        return;
                    }
                    if (task.getStatus() != RecommendTaskStatus.CONFIRMED
                            && task.getStatus() != RecommendTaskStatus.GENERATING) {
                        return;
                    }

                    task.setLastHeartbeatAt(Instant.now());
                    int progress = task.getProgress() == null
                            ? progressForStatus(RecommendTaskStatus.GENERATING)
                            : task.getProgress();
                    task.setProgress(Math.min(progress + 1, 95));
                    recommendTaskRepository.save(task);
                }),
                HEARTBEAT_INTERVAL_SECONDS,
                HEARTBEAT_INTERVAL_SECONDS,
                TimeUnit.SECONDS
        );

        heartbeatFutures.put(taskId, future);
    }

    private void cancelHeartbeat(String taskId) {
        ScheduledFuture<?> future = heartbeatFutures.remove(taskId);
        if (future != null) {
            future.cancel(true);
        }
    }

    private RecommendTaskStatus resolveStatusFromFastApi(
            String rawStatus,
            boolean awaiting,
            List<String> subQuestions,
            String error
    ) {
        if (trimToNull(error) != null) {
            return RecommendTaskStatus.FAILED;
        }

        String normalized = rawStatus == null ? "" : rawStatus.trim().toUpperCase(Locale.ROOT);
        if ("PENDING_INPUT".equals(normalized) || "AWAITING_INPUT".equals(normalized)) return RecommendTaskStatus.PENDING_INPUT;
        if ("CANCELLED".equals(normalized)) return RecommendTaskStatus.CANCELLED;
        if ("INCOMPLETE".equals(normalized)) return RecommendTaskStatus.INCOMPLETE;
        if (awaiting
                || !subQuestions.isEmpty()
                || "PENDING_CONFIRM".equals(normalized)
                || "AWAITING_HUMAN_CONFIRMATION".equals(normalized)) {
            return RecommendTaskStatus.PENDING_CONFIRM;
        }

        if ("FAILED".equals(normalized) || "ERROR".equals(normalized)) {
            return RecommendTaskStatus.FAILED;
        }
        if ("EXPIRED".equals(normalized)) {
            return RecommendTaskStatus.EXPIRED;
        }
        if ("DONE".equals(normalized) || "SUCCESS".equals(normalized)) {
            return RecommendTaskStatus.DONE;
        }

        if ("CONFIRMED".equals(normalized) || "GENERATING".equals(normalized)) {
            return RecommendTaskStatus.GENERATING;
        }

        return RecommendTaskStatus.GENERATING;
    }

    private RecommendTaskStateResponse toResponse(RecommendTaskEntity task) {
        RecommendTaskStatus status = task.getStatus();
        List<String> subQuestions = status == RecommendTaskStatus.PENDING_CONFIRM
                ? deserializeSubQuestions(task.getSubQuestions())
                : List.of();
        String finalResult = (status == RecommendTaskStatus.DONE || status == RecommendTaskStatus.INCOMPLETE)
                ? firstNonBlank(task.getFinalResult(), "")
                : "";
        String errorMessage = isFailureStatus(status) ? task.getErrorMessage() : null;

        RecommendTaskStateResponse response = new RecommendTaskStateResponse();
        response.setTaskId(task.getId());
        response.setConversationId(task.getConversation() == null ? null : task.getConversation().getId());
        response.setStatus(status.name());
        response.setPendingInput(status == RecommendTaskStatus.PENDING_INPUT ? deserializeMap(task.getPendingInput()) : null);
        response.setSubQuestions(subQuestions);
        response.setFinalResult(finalResult);
        if (status == RecommendTaskStatus.DONE || status == RecommendTaskStatus.INCOMPLETE) {
            response.setResultMetadata(deserializeMap(task.getResultMetadata()));
        }
        response.setErrorMessage(errorMessage);
        response.setProgress(task.getProgress());
        response.setLastHeartbeatAt(task.getLastHeartbeatAt());
        response.setExpireAt(task.getExpireAt());
        response.setCreatedAt(task.getCreatedAt());
        response.setUpdatedAt(task.getUpdatedAt());
        return response;
    }

    /*
     * Canonical HITL flow:
     * IDLE -> GENERATING -> PENDING_CONFIRM -> GENERATING -> DONE/FAILED/EXPIRED
     *
     * Field semantics:
     * - sub_questions is meaningful only in PENDING_CONFIRM
     * - final_result is meaningful only in DONE
     * - error_message is meaningful only in FAILED/EXPIRED
     */
    private void applyCanonicalTaskState(
            RecommendTaskEntity task,
            RecommendTaskStatus status,
            List<String> subQuestions,
            String finalResult,
            String errorMessage
    ) {
        task.setStatus(status);
        if (status != RecommendTaskStatus.PENDING_INPUT) task.setPendingInput(null);
        task.setSubQuestions(
                serializeSubQuestions(
                        status == RecommendTaskStatus.PENDING_CONFIRM
                                ? subQuestions
                                : List.of()
                )
        );
        task.setFinalResult((status == RecommendTaskStatus.DONE || status == RecommendTaskStatus.INCOMPLETE) ? trimToNull(finalResult) : null);
        task.setErrorMessage(isFailureStatus(status) ? trimToNull(errorMessage) : null);
    }

    private RecommendTaskStateResponse expiredResponse(String taskId, String message) {
        RecommendTaskStateResponse response = new RecommendTaskStateResponse();
        response.setTaskId(taskId);
        response.setStatus(RecommendTaskStatus.EXPIRED.name());
        response.setSubQuestions(List.of());
        response.setFinalResult("");
        response.setErrorMessage(message);
        response.setProgress(progressForStatus(RecommendTaskStatus.EXPIRED));
        response.setLastHeartbeatAt(Instant.now());
        response.setCreatedAt(Instant.now());
        response.setUpdatedAt(Instant.now());
        return response;
    }

    private String normalizeAction(String action) {
        String normalized = firstNonBlank(action, "confirm");
        if (normalized == null) {
            return "confirm";
        }
        normalized = normalized.toLowerCase(Locale.ROOT);
        return "edit".equals(normalized) ? "edit" : "confirm";
    }

    private List<String> deserializeSubQuestions(String raw) {
        String normalized = trimToNull(raw);
        if (normalized == null) {
            return List.of();
        }

        try {
            Object parsed = objectMapper.readValue(normalized, new TypeReference<>() {
            });
            return normalizeSubQuestions(parsed);
        } catch (Exception ignored) {
            return normalizeSubQuestions(normalized);
        }
    }

    private String serializeSubQuestions(List<String> subQuestions) {
        List<String> normalized = normalizeSubQuestions(subQuestions);
        if (normalized.isEmpty()) {
            return "[]";
        }
        try {
            return objectMapper.writeValueAsString(normalized);
        } catch (Exception ignored) {
            return String.join("\n", normalized);
        }
    }

    private List<String> normalizeSubQuestions(Object raw) {
        if (raw == null) {
            return List.of();
        }

        Set<String> unique = new LinkedHashSet<>();
        collectSubQuestions(raw, unique, 0);
        return new ArrayList<>(unique);
    }

    private void collectSubQuestions(Object raw, Set<String> output, int depth) {
        if (raw == null || depth > 12) {
            return;
        }

        if (raw instanceof String textValue) {
            String text = trimToNull(textValue);
            if (text == null) {
                return;
            }

            Object parsed = tryParseStructuredText(text);
            if (parsed != null) {
                collectSubQuestions(parsed, output, depth + 1);
                return;
            }

            if (looksLikeSerializedSubQuestionPayload(text)) {
                List<String> extracted = extractQuotedSegments(text);
                for (String item : extracted) {
                    String normalized = trimToNull(item);
                    if (normalized != null) {
                        output.add(normalized);
                    }
                }
                return;
            }

            output.add(text);
            return;
        }

        if (raw instanceof Map<?, ?> mapValue) {
            List<Object> preferred = new ArrayList<>();
            for (Map.Entry<?, ?> entry : mapValue.entrySet()) {
                String key = entry.getKey() == null ? "" : entry.getKey().toString().trim().toLowerCase(Locale.ROOT);
                if ("sub_questions".equals(key) || "pending_sub_questions".equals(key)) {
                    preferred.add(entry.getValue());
                }
            }
            if (!preferred.isEmpty()) {
                for (Object value : preferred) {
                    collectSubQuestions(value, output, depth + 1);
                }
                return;
            }
            for (Object value : mapValue.values()) {
                collectSubQuestions(value, output, depth + 1);
            }
            return;
        }

        if (raw instanceof Iterable<?> iterable) {
            for (Object value : iterable) {
                collectSubQuestions(value, output, depth + 1);
            }
            return;
        }

        if (raw.getClass().isArray()) {
            int length = Array.getLength(raw);
            for (int i = 0; i < length; i++) {
                collectSubQuestions(Array.get(raw, i), output, depth + 1);
            }
            return;
        }

        String text = trimToNull(raw.toString());
        if (text != null) {
            output.add(text);
        }
    }

    private Object tryParseStructuredText(String text) {
        String normalized = trimToNull(text);
        if (normalized == null || normalized.isEmpty()) {
            return null;
        }
        char start = normalized.charAt(0);
        if (start != '{' && start != '[') {
            return null;
        }
        try {
            return objectMapper.readValue(normalized, Object.class);
        } catch (Exception ignored) {
            return null;
        }
    }

    private boolean looksLikeSerializedSubQuestionPayload(String text) {
        String normalized = trimToNull(text);
        if (normalized == null || normalized.isEmpty()) {
            return false;
        }
        if (normalized.charAt(0) != '{' && normalized.charAt(0) != '[') {
            return false;
        }
        String lowered = normalized.toLowerCase(Locale.ROOT);
        return lowered.contains("sub_questions") || lowered.contains("pending_sub_questions");
    }

    private List<String> extractQuotedSegments(String text) {
        List<String> extracted = new ArrayList<>();
        Matcher matcher = QUOTED_STRING_PATTERN.matcher(text);
        while (matcher.find()) {
            String candidate = matcher.group(1) != null ? matcher.group(1) : matcher.group(2);
            String normalized = trimToNull(candidate);
            if (normalized == null) {
                continue;
            }
            String lowered = normalized.toLowerCase(Locale.ROOT);
            if ("sub_questions".equals(lowered) || "pending_sub_questions".equals(lowered)) {
                continue;
            }
            extracted.add(normalized);
        }
        return extracted;
    }

    private int progressForStatus(RecommendTaskStatus status) {
        return switch (status) {
            case PENDING_CONFIRM, PENDING_INPUT -> 20;
            case CONFIRMED -> 35;
            case GENERATING -> 60;
            case DONE, FAILED, EXPIRED, CANCELLED, INCOMPLETE -> 100;
        };
    }

    private void appendAssistantIfNeeded(String conversationId, String content) {
        String normalizedContent = trimToNull(content);
        if (normalizedContent == null) {
            return;
        }

        List<ConversationMessageEntity> recentMessages = conversationService.getRecentMessages(conversationId, 1);
        if (!recentMessages.isEmpty()) {
            ConversationMessageEntity latest = recentMessages.get(0);
            String latestRole = firstNonBlank(latest.getRole(), "");
            String latestContent = firstNonBlank(latest.getContent(), "");
            if ("assistant".equalsIgnoreCase(latestRole) && normalizedContent.equals(latestContent)) {
                return;
            }
        }

        conversationService.appendMessage(conversationId, "assistant", normalizedContent);
    }

    private boolean isTerminal(RecommendTaskStatus status) {
        return status == RecommendTaskStatus.DONE
                || status == RecommendTaskStatus.CANCELLED
                || status == RecommendTaskStatus.INCOMPLETE
                || status == RecommendTaskStatus.FAILED
                || status == RecommendTaskStatus.EXPIRED;
    }

    private boolean isFailureStatus(RecommendTaskStatus status) {
        return status == RecommendTaskStatus.FAILED || status == RecommendTaskStatus.EXPIRED;
    }

    private String asText(Object value) {
        if (value == null) {
            return null;
        }
        return value.toString();
    }

    private String toErrorMessage(Exception ex) {
        String message = trimToNull(ex.getMessage());
        return firstNonBlank(message, ex.getClass().getSimpleName(), "unexpected error");
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

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim();
        return normalized.isEmpty() ? null : normalized;
    }

    @PreDestroy
    public void shutdownExecutors() {
        for (Map.Entry<String, ScheduledFuture<?>> entry : heartbeatFutures.entrySet()) {
            ScheduledFuture<?> future = entry.getValue();
            if (future != null) {
                future.cancel(true);
            }
        }
        heartbeatFutures.clear();
        confirmExecutor.shutdownNow();
        heartbeatExecutor.shutdownNow();
    }

    public record RecommendTaskCreateOutcome(
            RecommendTaskStateResponse taskState,
            String assistantMessage,
            String fastapiSessionId,
            boolean awaitingHumanConfirmation,
            List<String> pendingSubQuestions,
            List<Map<String, Object>> retrievedImages
    ) {
    }

    public record ConfirmStreamPreparation(
            RecommendTaskStateResponse taskState,
            String sessionId,
            String action,
            List<String> subQuestions,
            String comment
    ) {
    }
}

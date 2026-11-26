package com.example.demo.conversation.service;

import com.example.demo.dto.OllamaChatRequest;
import com.example.demo.dto.RecommendTaskStateResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectWriter;
import jakarta.persistence.EntityManager;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.io.OutputStream;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.function.Supplier;

@Service
public class ChatSubmissionService {
    private static final ObjectWriter FINGERPRINT_JSON = new ObjectMapper().writer();
    private record SubmissionKey(String owner, String id) { }
    private record Flight(String fingerprint, CompletableFuture<Object> accepted) { }
    public record RequestKey(String id, String fingerprint) { }
    public record GateResult<T>(T value, boolean leader) { }
    private final ConcurrentMap<SubmissionKey, Flight> preparing = new ConcurrentHashMap<>();
    private final JdbcTemplate jdbc;
    private final EntityManager entityManager;
    private final RecommendTaskStateService tasks;

    public ChatSubmissionService(JdbcTemplate jdbc, EntityManager entityManager,
                                 RecommendTaskStateService tasks) {
        this.jdbc = jdbc;
        this.entityManager = entityManager;
        this.tasks = tasks;
    }

    public RequestKey key(String requestId, OllamaChatRequest payload) {
        return requestId == null ? null : new RequestKey(canonicalId(requestId), fingerprint(payload));
    }

    /** Coalesce routing and initial commit on this instance. The primary key protects all instances. */
    public <T> GateResult<T> prepareOnce(String owner, RequestKey request,
                                         Class<T> resultType, Supplier<T> prepare) {
        if (request == null) return new GateResult<>(prepare.get(), true);
        var key = new SubmissionKey(owner, request.id());
        var flight = new Flight(request.fingerprint(), new CompletableFuture<>());
        var existing = preparing.putIfAbsent(key, flight);
        if (existing != null) {
            requireSamePayload(existing.fingerprint(), flight.fingerprint());
            try {
                return new GateResult<>(resultType.cast(existing.accepted().join()), false);
            } catch (CompletionException failure) {
                if (failure.getCause() instanceof RuntimeException cause) throw cause;
                if (failure.getCause() instanceof Error cause) throw cause;
                throw failure;
            }
        }
        try {
            T result = prepare.get();
            flight.accepted().complete(result);
            return new GateResult<>(result, true);
        } catch (RuntimeException | Error failure) {
            flight.accepted().completeExceptionally(failure);
            throw failure;
        } finally {
            preparing.remove(key, flight);
        }
    }

    @Transactional(readOnly = true)
    public String findReplay(String owner, RequestKey request) {
        if (request == null) return null;
        return lookup(owner, request);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public String reserve(String owner, RequestKey request) {
        if (request == null) return null;
        // Even the first turn has an owner row. Its lock makes insertion portable across H2 and PostgreSQL.
        jdbc.queryForObject("SELECT id FROM app_users WHERE id = ? FOR UPDATE", String.class, owner);
        String existing = lookup(owner, request);
        if (existing != null) return existing;
        jdbc.update("INSERT INTO chat_submissions(owner_user_id, request_id, payload_hash) VALUES (?, ?, ?)",
                owner, request.id(), request.fingerprint());
        return null;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void bind(String owner, RequestKey request, String taskId) {
        if (request == null) return;
        // The JDBC foreign key needs to see the task inserted by JPA in this transaction.
        entityManager.flush();
        int count = jdbc.update("UPDATE chat_submissions SET task_id = ? WHERE owner_user_id = ? AND request_id = ? AND task_id IS NULL",
                taskId, owner, request.id());
        if (count != 1) throw new IllegalStateException("Missing chat submission reservation");
    }

    private String lookup(String owner, RequestKey request) {
        return jdbc.query("SELECT payload_hash, task_id FROM chat_submissions WHERE owner_user_id = ? AND request_id = ?",
                result -> {
                    if (!result.next()) return null;
                    requireSamePayload(result.getString("payload_hash"), request.fingerprint());
                    String taskId = result.getString("task_id");
                    // The reservation and task commit together. A later hard delete clears the foreign key.
                    if (taskId == null) throw new ResponseStatusException(HttpStatus.GONE, "Original chat task was deleted");
                    return taskId;
                }, owner, request.id());
    }

    private void requireSamePayload(String expected, String supplied) {
        if (!expected.equals(supplied))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Request ID belongs to a different message");
    }

    private String canonicalId(String id) {
        try {
            String canonical = UUID.fromString(id).toString();
            if (canonical.equalsIgnoreCase(id)) return canonical;
        } catch (IllegalArgumentException ignored) {
            // Map malformed and non-canonical IDs to the same client error.
        }
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "X-Request-Id must be a UUID");
    }

    private String fingerprint(OllamaChatRequest request) {
        // Explicit order keeps the digest stable if DTO field declarations or Jackson naming change.
        var messages = request.getMessages() == null ? List.of() : request.getMessages().stream()
                .map(m -> m == null ? null : Arrays.asList(m.getRole(), m.getContent())).toList();
        var images = request.getImages() == null ? List.of() : request.getImages().stream()
                .map(i -> i == null ? null : Arrays.asList(i.getName(), i.getMediaType(), i.getDataUrl())).toList();
        var fields = Arrays.asList(request.getModel(), request.getRoutingMode(), request.getConversationId(),
                request.getCurrentConversationId(), request.getSessionId(), request.getThreadId(), messages, images);
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            FINGERPRINT_JSON.writeValue(new DigestOutputStream(OutputStream.nullOutputStream(), digest), fields);
            return HexFormat.of().formatHex(digest.digest());
        } catch (IOException | NoSuchAlgorithmException e) {
            throw new IllegalStateException("Cannot fingerprint chat submission", e);
        }
    }

    public SseEmitter replay(String owner, String taskId) {
        RecommendTaskStateResponse task = tasks.getOwnedTaskState(owner, taskId);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("task_id", taskId);
        data.put("conversation_id", task.getConversationId());
        data.put("session_id", task.getConversationId());
        data.put("replayed", true);
        String event = switch (task.getStatus()) {
            case "GENERATING", "CONFIRMED" -> {
                data.put("status", task.getStatus());
                yield "meta";
            }
            case "PENDING_INPUT" -> {
                data.put("pending_input", task.getPendingInput());
                yield "awaiting_input";
            }
            case "PENDING_CONFIRM" -> {
                data.put("sub_questions", task.getSubQuestions());
                yield "awaiting_confirmation";
            }
            case "DONE", "INCOMPLETE" -> {
                data.put("status", "DONE".equals(task.getStatus()) ? "success" : "incomplete");
                data.put("final_answer", task.getFinalResult());
                yield "final";
            }
            default -> {
                data.put("message", "Chat task ended with status " + task.getStatus() + " (" + taskId + ")");
                yield "error";
            }
        };
        data.put("type", event);
        SseEmitter emitter = new SseEmitter();
        try {
            emitter.send(SseEmitter.event().name(event).data(data, MediaType.APPLICATION_JSON));
            emitter.complete();
        } catch (IOException failure) {
            emitter.completeWithError(failure);
        }
        return emitter;
    }
}

package com.example.demo.routing;

import com.example.demo.auth.BusinessAccess;
import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.service.ConversationService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Resolves ownership before persistence. Shadow predictions never become execution input. */
@Service
public class RoutingPreflight {
    private final BusinessAccess access;
    private final ConversationService conversations;
    private final FastApiClient agent;
    private final String deployment;
    private final boolean automaticApproved;
    private final int trafficPercent;
    private final SessionRoutingGate gate;

    public RoutingPreflight(BusinessAccess access, ConversationService conversations, FastApiClient agent, SessionRoutingGate gate,
            @Value("${app.routing.mode:shadow}") String deployment,
            @Value("${app.routing.auto-session-approved:false}") boolean automaticApproved,
            @Value("${app.routing.traffic-percent:0}") int trafficPercent) {
        this.access = access;
        this.gate = gate;
        this.conversations = conversations;
        this.agent = agent;
        this.deployment = deployment;
        this.automaticApproved = automaticApproved;
        this.trafficPercent = Math.max(0, Math.min(100, trafficPercent));
        if (!Set.of("off", "shadow", "live").contains(deployment))
            throw new IllegalArgumentException("app.routing.mode must be off, shadow or live");
    }

    public Outcome resolve(String owner, String query, boolean hasImages, String mode,
            String conversationId, String sessionId, String threadId, String currentConversationId,
            int timeoutSeconds) {
        long started = System.nanoTime();
        if (timeoutSeconds < 10 || timeoutSeconds > 300)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "timeout must be between 10 and 300 seconds");
        String routingMode = clean(mode);
        if (routingMode != null && !Set.of("explicit", "auto", "new").contains(routingMode))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid routing_mode");
        String target = access.matchingConversation(owner, clean(conversationId), clean(sessionId));
        // A thread is a conversation-level identity, not a legacy task alias.
        if (clean(threadId) != null) {
            String thread = access.conversation(owner, threadId.trim()).getId();
            if (target != null && !target.equals(thread))
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Resource not found");
            target = thread;
        }
        if ("new".equals(routingMode) && target != null)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "new cannot specify an existing target");
        String candidate = clean(currentConversationId);
        if (candidate != null) candidate = access.conversation(owner, candidate).getId();
        boolean unresolved = target == null && "auto".equals(routingMode);
        String contextId = target != null ? target : unresolved ? candidate : null;
        // Server constructs this only after ownership checks; browsers cannot pick a rollout bucket.
        String cohortKey = owner + ":" + (contextId == null ? "new" : contextId);
        Map<String, Object> context = new LinkedHashMap<>();
        if (contextId != null) {
            context.put("facts", conversations.getFacts(contextId));
            var recent = new ArrayList<>(conversations.getRecentMessages(contextId, 6));
            java.util.Collections.reverse(recent);
            context.put("messages", recent.stream()
                    .map(message -> Map.of("role", message.getRole(), "content", message.getContent())).toList());
        }
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("snapshot_id", UUID.randomUUID().toString());
        snapshot.put("request_id", snapshot.get("snapshot_id"));
        snapshot.put("cohort_key", cohortKey);
        snapshot.put("occurred_at", java.time.Instant.now().toString());
        snapshot.put("query", query == null ? "" : query);
        snapshot.put("binding", target != null ? "existing" : unresolved ? "unresolved" : "new");
        snapshot.put("target_session_id", target);
        snapshot.put("current_conversation_id", unresolved ? candidate : null);
        snapshot.put("context", context);
        snapshot.put("has_images", hasImages);
        snapshot.put("reviews_used", 0);
        snapshot.put("timeout_seconds", Math.min(8, timeoutSeconds));
        boolean active = "live".equals(deployment)
                && cohortBucket(cohortKey) < trafficPercent;
        if (!active) {
            if (!"off".equals(deployment)) agent.shadowRouting(snapshot);
            return unresolved ? clarification(candidate, "请选择这条消息所属的会话，或新建会话。", timeoutSeconds)
                    : new Outcome(target, null, null, timeoutSeconds);
        }
        if (unresolved && (!automaticApproved || !gate.accepted())) {
            agent.shadowRouting(snapshot);
            return clarification(candidate, "自动归属尚在验证中，请明确选择会话。", timeoutSeconds);
        }
        Map<String, Object> decision;
        try {
            Map<String, Object> received = agent.resolveRouting(snapshot);
            decision = received == null ? null : new LinkedHashMap<>(received);
            if (decision == null || !"intent-routing.v1".equals(decision.get("schema_version")))
                throw new IllegalArgumentException("Invalid routing response version");
        } catch (Exception failure) {
            if (unresolved) return clarification(candidate, "暂时无法确定消息归属，请选择会话。", timeoutSeconds);
            decision = new LinkedHashMap<>();
            decision.put("schema_version", "intent-routing.v1");
            decision.put("snapshot_id", snapshot.get("snapshot_id"));
            decision.put("target", target == null ? "new" : "existing");
            decision.put("target_session_id", target);
            decision.put("status", "ready");
            decision.put("mode", "rag");
            decision.put("request_type", "uncertain");
            decision.put("task_relation", target == null ? "new" : "continue");
            decision.put("source", "fallback");
            decision.put("reviews_used", 0);
        }
        double remaining = timeoutSeconds - (System.nanoTime() - started) / 1_000_000_000.0;
        if (remaining <= 0) throw new ResponseStatusException(HttpStatus.GATEWAY_TIMEOUT, "Request budget exhausted");
        if ("routing_clarification".equals(decision.get("status")))
            return clarification(contextId, "请补充更明确的目标，并选择会话归属。", remaining);
        if (!"ready".equals(decision.get("status"))
                || !Set.of("rag", "direct", "hitl").contains(String.valueOf(decision.get("mode"))))
            return clarification(candidate, "路由结果无效，请明确选择会话。", remaining);
        if (unresolved) {
            if ("existing".equals(decision.get("target")) && candidate != null
                    && candidate.equals(decision.get("target_session_id"))) target = candidate;
            else if ("new".equals(decision.get("target"))) target = null;
            else return clarification(candidate, "请明确选择会话。", remaining);
        }
        if (target != null) access.conversation(owner, target);
        decision.put("target", target == null ? "new" : "existing");
        decision.put("target_session_id", target);
        decision.put("task_relation", target == null ? "new" : "continue");
        return new Outcome(target, java.util.Collections.unmodifiableMap(new LinkedHashMap<>(decision)), null, remaining);
    }

    private Outcome clarification(String candidate, String prompt, double remaining) {
        List<Map<String, Object>> options = new ArrayList<>();
        if (candidate != null) options.add(Map.of("kind", "existing", "conversation_id", candidate,
                "title", conversations.findActiveConversation(candidate).getTitle()));
        options.add(Map.of("kind", "new", "title", "新建会话"));
        return new Outcome(null, null, Map.of("clarification_id", UUID.randomUUID().toString(),
                "kind", "routing", "prompt", prompt, "options", options), remaining);
    }

    private static String clean(String value) { return value == null || value.isBlank() ? null : value.trim(); }

    /** Shared with Python: SHA-256 UTF-8, unsigned first 64 bits, modulo 100. */
    static int cohortBucket(String key) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
            return (int) Long.remainderUnsigned(ByteBuffer.wrap(digest).getLong(), 100);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    public record Outcome(String conversationId, Map<String, Object> decision,
            Map<String, Object> clarification, double remainingSeconds) {
        public boolean needsClarification() { return clarification != null; }
        public Map<String, Object> clarificationPayload() {
            return Map.of("type", "routing_clarification", "status", "routing_clarification", "clarification", clarification);
        }
    }
}

package com.example.demo.auth;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"spring.datasource.url=${CHAT_TEST_DB_URL:jdbc:h2:mem:auth_integration;DB_CLOSE_DELAY=-1}", "app.routing.mode=off"})
class BusinessAuthIntegrationTests {
    static final ObjectMapper JSON = new ObjectMapper();
    static final List<String> CALLS = new CopyOnWriteArrayList<>();
    static final List<String> KEYS = new CopyOnWriteArrayList<>();
    static final Map<String, Integer> CLARIFICATION_REJECTIONS = new java.util.concurrent.ConcurrentHashMap<>();
    static final Map<String, Runnable> BEFORE_STREAM_REPLY = new java.util.concurrent.ConcurrentHashMap<>();
    static final Map<String, Runnable> DURING_FACT_SYNC = new java.util.concurrent.ConcurrentHashMap<>();
    static final HttpServer AGENT = createAgent();
    @Autowired TestRestTemplate http;
    @Autowired JdbcTemplate database;
    @Autowired jakarta.persistence.EntityManagerFactory entities;
    @Autowired UserRepository users;
    @Autowired JwtEncoder encoder;
    @Autowired com.example.demo.conversation.repository.ConversationRepository conversations;
    @Autowired com.example.demo.conversation.repository.RecommendTaskRepository tasks;
    @Autowired com.example.demo.conversation.repository.ConversationMessageRepository messages;
    @Autowired com.example.demo.conversation.service.RecommendTaskStateService taskStates;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("app.fastapi.base-url", () -> "http://127.0.0.1:" + AGENT.getAddress().getPort());
    }
    @AfterAll static void stopAgent() { AGENT.stop(0); }

    static HttpServer createAgent() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                String path = exchange.getRequestURI().getPath();
                CALLS.add(path);
                String key = exchange.getRequestHeaders().getFirst("X-API-Key");
                KEYS.add(key == null ? "missing" : key);
                if (!"test-agent-key".equals(key)) {
                    exchange.sendResponseHeaders(401, -1); exchange.close(); return;
                }
                JsonNode request = JSON.readTree(exchange.getRequestBody().readAllBytes());
                String session = request == null ? "" : request.path("session_id").asText();
                if (path.contains("/session-state/") && "PUT".equals(exchange.getRequestMethod())) {
                    Runnable observer = DURING_FACT_SYNC.remove(path.substring(path.lastIndexOf('/') + 1));
                    if (observer != null) observer.run();
                }
                Integer rejection = path.contains("clarify") ? CLARIFICATION_REJECTIONS.remove(session) : null;
                if (rejection != null) {
                    if (rejection > 0) exchange.sendResponseHeaders(rejection, -1);
                    exchange.close(); return;
                }
                String result;
                if (path.contains("assets")) {
                    byte[] image = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jk1sAAAAASUVORK5CYII=");
                    exchange.getResponseHeaders().set("Content-Type", "image/png");
                    exchange.sendResponseHeaders(200, image.length);
                    exchange.getResponseBody().write(image); exchange.close(); return;
                } else if (path.endsWith("stream")) {
                    Runnable observer = BEFORE_STREAM_REPLY.remove(session);
                    if (observer != null) observer.run();
                    boolean pending = !path.contains("confirm") && request.path("query").asText().contains("needs confirmation");
                    result = "event: meta\ndata: {\"type\":\"meta\",\"session_id\":\"" + session + "\"}\n\n";
                    if (path.contains("clarify")) result = "event: resume_accepted\ndata: " + JSON.writeValueAsString(Map.of(
                            "session_id", session, "kind", "clarification", "clarification_id", request.path("clarification_id").asText())) + "\n\n" + result;
                    if (request.path("query").asText().contains("end before final")) {
                        result += "event: token\ndata: {\"type\":\"token\",\"delta\":\"Partial\"}\n\n";
                    } else if (request.path("query").asText().contains("needs input")) result += "event: awaiting_input\ndata: {\"type\":\"awaiting_input\",\"pending_input\":{\"clarification_id\":\"input-1\",\"kind\":\"execution\",\"prompt\":\"Which deployment?\"}}\n\n";
                    else if (pending) result += "event: awaiting_confirmation\ndata: {\"type\":\"awaiting_confirmation\",\"sub_questions\":[\"Which database?\"]}\n\n";
                    else {
                        result += "event: token\ndata: {\"type\":\"token\",\"delta\":\"Hello\"}\n\nevent: token\ndata: {\"type\":\"token\",\"delta\":\" \"}\n\nevent: token\ndata: {\"type\":\"token\",\"delta\":\"world\"}\n\n";
                        result += "event: final\ndata: " + JSON.writeValueAsString(Map.of("type", "final", "status", "success",
                                "final_answer", "Hello world", "retrieved_doc_ids", List.of("doc-1"),
                                "retrieved_images", List.of(Map.of("doc_id", "doc-1", "url", "/api/v1/assets/example.png")))) + "\n\n";
                    }
                    exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
                } else {
                    boolean pending = path.endsWith("recommend") && request.path("query").asText().contains("needs confirmation");
                    result = "{\"status\":\"success\",\"session_id\":\"" + session + "\",\"final_answer\":\"Hello world\",\"awaiting_human_confirmation\":" + pending + ",\"pending_sub_questions\":[\"Which database?\"]}";
                    if (request.path("query").asText().contains("partial evidence")) result = JSON.writeValueAsString(Map.of(
                            "status", "incomplete", "session_id", session, "final_answer", "Only one requirement has evidence."));
                    if (request.path("query").asText().contains("needs input")) result = JSON.writeValueAsString(Map.of(
                            "status", "awaiting_input", "session_id", session,
                            "pending_input", Map.of("clarification_id", "input-1", "kind", "execution", "prompt", "Which deployment?")));
                    if (path.contains("clarify")) result = JSON.writeValueAsString(Map.of("status", "success", "session_id", session, "final_answer", "Clarified answer"));
                    exchange.getResponseHeaders().set("Content-Type", "application/json");
                }
                byte[] bytes = result.getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, bytes.length);
                exchange.getResponseBody().write(bytes); exchange.close();
            });
            server.start(); return server;
        } catch (Exception error) { throw new IllegalStateException(error); }
    }

    ResponseEntity<String> call(HttpMethod method, String path, String token, Object body) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        if (token != null) headers.setBearerAuth(token);
        return http.exchange(path, method, new HttpEntity<>(body, headers), String.class);
    }
    JsonNode json(ResponseEntity<String> response) throws Exception { return JSON.readTree(response.getBody()); }
    String login(String name) throws Exception {
        var response = call(HttpMethod.POST, "/api/auth/login", null,
                Map.of("username", name, "password", name + "-test-password"));
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        String token = json(response).path("access_token").asText();
        assertThat(token).isNotBlank(); return token;
    }
    String conversation(String token) throws Exception {
        var response = call(HttpMethod.POST, "/api/v1/conversations", token, Map.of());
        assertThat(response.getStatusCode().value()).isEqualTo(201);
        return json(response).path("conversation_id").asText();
    }

    ResponseEntity<String> sendChat(String token, String conversationId, String text, String requestId) {
        var body = new HashMap<String, Object>();
        body.put("messages", List.of(Map.of("role", "user", "content", text)));
        if (conversationId != null) body.put("conversation_id", conversationId);
        var headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setBearerAuth(token);
        headers.set("X-Request-Id", requestId);
        return http.postForEntity("/api/chat/stream", new HttpEntity<>(body, headers), String.class);
    }

    long messageCount(String conversationId, String role) {
        return database.queryForObject("SELECT count(*) FROM conversation_messages WHERE conversation_id=? AND role=?",
                Long.class, conversationId, role);
    }

    @Test void newChatRetriesReturnOriginalTaskAndDifferentKeysStartNewTurns() throws Exception {
        String token = login("alice"), key = UUID.randomUUID().toString();
        long initialConversations = conversations.count(), initialTasks = tasks.count();
        int initialRuns = Collections.frequency(CALLS, "/api/v1/recommend/stream");
        var first = sendChat(token, null, "normal question", key);
        assertThat(first.getBody()).contains("event:final");
        String taskId = database.queryForObject("SELECT task_id FROM chat_submissions WHERE request_id=?", String.class, key);
        String conversationId = database.queryForObject("SELECT conversation_id FROM recommend_tasks WHERE id=?", String.class, taskId);
        assertThat(sendChat(token, null, "normal question", key).getBody())
                .contains("event:final", taskId, conversationId, "Hello world");
        assertThat(sendChat(token, null, "changed question", key).getStatusCode().value()).isEqualTo(409);
        assertThat(conversations.count()).isEqualTo(initialConversations + 1);
        assertThat(tasks.count()).isEqualTo(initialTasks + 1);
        assertThat(messageCount(conversationId, "user")).isOne();
        assertThat(messageCount(conversationId, "assistant")).isOne();
        assertThat(Collections.frequency(CALLS, "/api/v1/recommend/stream")).isEqualTo(initialRuns + 1);
        assertThat(sendChat(token, conversationId, "normal question", UUID.randomUUID().toString()).getBody()).contains("event:final");
        assertThat(messageCount(conversationId, "user")).isEqualTo(2);
        assertThat(Collections.frequency(CALLS, "/api/v1/recommend/stream")).isEqualTo(initialRuns + 2);
    }

    @Test void runningRetryRestoresTheTaskWhileTheFirstStreamIsOpen() throws Exception {
        String token = login("alice"), id = conversation(token), key = UUID.randomUUID().toString();
        var generating = new java.util.concurrent.CountDownLatch(1);
        var finish = new java.util.concurrent.CountDownLatch(1);
        BEFORE_STREAM_REPLY.put(id, () -> {
            generating.countDown();
            try { finish.await(15, java.util.concurrent.TimeUnit.SECONDS); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        });
        var pool = java.util.concurrent.Executors.newFixedThreadPool(2);
        try {
            var first = pool.submit(() -> sendChat(token, id, "one logical send", key));
            assertThat(generating.await(10, java.util.concurrent.TimeUnit.SECONDS)).isTrue();
            var replay = pool.submit(() -> sendChat(token, id, "one logical send", key));
            String task = database.queryForObject("SELECT task_id FROM chat_submissions WHERE request_id=?", String.class, key);
            assertThat(replay.get(5, java.util.concurrent.TimeUnit.SECONDS).getBody()).contains(task, id, "replayed");
            finish.countDown();
            assertThat(first.get(10, java.util.concurrent.TimeUnit.SECONDS).getBody()).contains("event:final", task);
            assertThat(tasks.findByConversation_IdOrderByCreatedAtDesc(id)).hasSize(1);
            assertThat(messageCount(id, "user")).isOne();
        } finally { finish.countDown(); pool.shutdownNow(); }
    }

    @Test void finalReplyPreservesEditsAndHonorsDeletionOrAnEarlierFailure() throws Exception {
        String token = login("alice");
        String renamed = conversation(token);
        BEFORE_STREAM_REPLY.put(renamed, () -> database.update("UPDATE conversations SET title=?, pinned=true WHERE id=?", "edited", renamed));
        assertThat(sendChat(token, renamed, "recommend", UUID.randomUUID().toString()).getBody()).contains("event:final");
        var edited = conversations.findById(renamed).orElseThrow();
        assertThat(edited.getTitle()).isEqualTo("edited");
        assertThat(edited.isPinned()).isTrue();
        for (boolean delete : List.of(false, true)) {
            String id = conversation(token);
            BEFORE_STREAM_REPLY.put(id, () -> {
                if (delete) database.update("UPDATE conversations SET deleted=true, archived=true WHERE id=?", id);
                else database.update("UPDATE recommend_tasks SET status='FAILED', error_message='stalled' WHERE conversation_id=?", id);
            });
            var stream = sendChat(token, id, "recommend", UUID.randomUUID().toString());
            assertThat(stream.getBody()).contains("event:error").doesNotContain("event:final");
            assertThat(tasks.findByConversation_IdOrderByCreatedAtDesc(id).getFirst().getStatus().name()).isEqualTo("FAILED");
            assertThat(messageCount(id, "assistant")).isZero();
            assertThat(conversations.findById(id).orElseThrow().isDeleted()).isEqualTo(delete);
        }
    }

    @Test void cleanEofPersistsPartialTextAndReleasesTheConversation() throws Exception {
        String token = login("alice"), id = conversation(token);
        var stream = sendChat(token, id, "end before final", UUID.randomUUID().toString());
        assertThat(stream.getBody()).contains("event:final", "\"incomplete\"", "Partial");
        var task = tasks.findByConversation_IdOrderByCreatedAtDesc(id).getFirst();
        assertThat(task.getStatus().name()).isEqualTo("INCOMPLETE");
        assertThat(task.getFinalResult()).isEqualTo("Partial");
        assertThat(messageCount(id, "assistant")).isOne();
        assertThat(sendChat(token, id, "next question", UUID.randomUUID().toString()).getStatusCode().value()).isEqualTo(200);
    }

    @Test void failedTerminalWriteRollsBackTheAssistantMessage() throws Exception {
        String token = login("alice"), id = conversation(token);
        database.execute("ALTER TABLE recommend_tasks ADD CONSTRAINT test_final_write CHECK (conversation_id <> '" + id + "' OR status <> 'DONE')");
        try {
            var stream = sendChat(token, id, "recommend", UUID.randomUUID().toString());
            assertThat(stream.getBody()).contains("event:error").doesNotContain("event:final", "test_final_write");
            assertThat(messageCount(id, "assistant")).isZero();
            assertThat(tasks.findByConversation_IdOrderByCreatedAtDesc(id).getFirst().getStatus().name()).isEqualTo("FAILED");
        } finally {
            database.execute("ALTER TABLE recommend_tasks DROP CONSTRAINT test_final_write");
        }
    }

    @Test void remoteFactSyncCanAcquireTheCommittedConversationFromAnotherConnection() throws Exception {
        String token = login("alice"), id = conversation(token);
        var observed = new java.util.concurrent.atomic.AtomicBoolean();
        DURING_FACT_SYNC.put(id, () -> {
            // An independent connection during HTTP must see the task and acquire its conversation immediately.
            var row = database.queryForList("SELECT id FROM conversations WHERE id=? FOR UPDATE NOWAIT", id);
            observed.set(row.size() == 1 && tasks.findByConversation_IdOrderByCreatedAtDesc(id).size() == 1);
        });
        assertThat(sendChat(token, id, "recommend", UUID.randomUUID().toString()).getBody()).contains("event:final");
        assertThat(observed.get()).isTrue();
    }

    @Test void postgresChatReservationDoesNotLoadCompletedTasksFromLongHistories() throws Exception {
        Assumptions.assumeTrue(System.getenv("CHAT_TEST_DB_URL") != null, "Uses PostgreSQL generate_series");
        String token = login("alice");
        var statistics = entities.unwrap(org.hibernate.SessionFactory.class).getStatistics();
        boolean previous = statistics.isStatisticsEnabled();
        statistics.setStatisticsEnabled(true);
        try {
            for (int historySize : List.of(10, 10_000)) {
                String id = conversation(token);
                database.update("""
                        INSERT INTO recommend_tasks (id, conversation_id, fastapi_session_id, status, created_at, updated_at)
                        SELECT md5(? || n::text), ?, ?, 'DONE', current_timestamp, current_timestamp
                        FROM generate_series(1, ?) AS n
                        """, id, id, id, historySize);
                var loaded = statistics.getEntityStatistics(com.example.demo.conversation.entity.RecommendTaskEntity.class.getName());
                long before = loaded.getLoadCount();
                assertThat(sendChat(token, id, "recommend", UUID.randomUUID().toString()).getBody()).contains("event:final");
                long taskRows = loaded.getLoadCount() - before;
                assertThat(taskRows).as("task rows loaded by the actual chat endpoint, with %d completed", historySize)
                        .isLessThan(10);
                System.out.printf("Chat history benchmark: %d completed tasks, %d task rows loaded%n", historySize, taskRows);
            }
        } finally {
            statistics.setStatisticsEnabled(previous);
        }
    }

    @Test void loginHashesPasswordsAndRejectsAnonymousEntry() throws Exception {
        String alice = login("alice");
        assertThat(users.findByUsername("alice").orElseThrow().getPasswordHash()).startsWith("$2").doesNotContain("alice-test-password");
        assertThat(json(call(HttpMethod.GET, "/api/auth/me", alice, null)).path("username").asText()).isEqualTo("alice");
        int before = CALLS.size();
        for (String route : List.of("/api/chat/stream", "/api/v1/recommend", "/api/v1/recommend/stream", "/api/chat/confirm/stream"))
            assertThat(call(HttpMethod.POST, route, null, Map.of()).getStatusCode().value()).isEqualTo(401);
        assertThat(call(HttpMethod.GET, "/api/conversations", null, null).getStatusCode().value()).isEqualTo(401);
        assertThat(call(HttpMethod.GET, "/api/v1/assets/test.png", null, null).getStatusCode().value()).isEqualTo(401);
        assertThat(CALLS).hasSize(before);
        assertThat(call(HttpMethod.POST, "/api/auth/login", null, Map.of("username", "alice", "password", "wrong-password")).getStatusCode().value()).isEqualTo(401);
        assertThat(call(HttpMethod.GET, "/h2-console", alice, null).getStatusCode().value()).isEqualTo(403);
    }

    @Test void rejectsInvalidTokensAndDisabledUsers() throws Exception {
        String userId = users.findByUsername("alice").orElseThrow().getId();
        for (String variant : List.of("expired", "issuer", "audience", "missing-audience", "missing-expiry", "unknown-user")) {
            Instant now = Instant.now();
            JwtClaimsSet.Builder builder = JwtClaimsSet.builder().subject(variant.equals("unknown-user") ? UUID.randomUUID().toString() : userId)
                    .issuer(variant.equals("issuer") ? "other" : AuthConfig.ISSUER)
                    .issuedAt(now.minusSeconds(7200));
            if (!variant.equals("missing-audience")) builder.audience(List.of(variant.equals("audience") ? "other" : AuthConfig.AUDIENCE));
            if (!variant.equals("missing-expiry")) builder.expiresAt(variant.equals("expired") ? now.minusSeconds(120) : now.plusSeconds(1800));
            JwtClaimsSet claims = builder.build();
            String token = encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
            assertThat(call(HttpMethod.GET, "/api/auth/me", token, null).getStatusCode().value()).isEqualTo(401);
        }
        String valid = login("alice");
        String[] parts = valid.split("\\.");
        parts[2] = (parts[2].startsWith("a") ? "b" : "a") + parts[2].substring(1);
        assertThat(call(HttpMethod.GET, "/api/auth/me", String.join(".", parts), null).getStatusCode().value()).isEqualTo(401);
        AppUser bob = users.findByUsername("bob").orElseThrow();
        String token = login("bob");
        bob.setEnabled(false); users.save(bob);
        try { assertThat(call(HttpMethod.GET, "/api/auth/me", token, null).getStatusCode().value()).isEqualTo(401); }
        finally { bob.setEnabled(true); users.save(bob); }
    }

    @Test void scopesConversationsAndEveryAliasBeforeAgentCalls() throws Exception {
        String alice = login("alice"), bob = login("bob"), id = conversation(alice), bId = conversation(bob);
        int before = CALLS.size();
        for (HttpMethod method : List.of(HttpMethod.GET, HttpMethod.PUT))
            assertThat(call(method, "/api/session-state/" + id, bob, method == HttpMethod.PUT ? Map.of("facts", Map.of("user_name", "intruder")) : null).getStatusCode().value()).isEqualTo(404);
        assertThat(call(HttpMethod.PATCH, "/api/conversations/" + id, bob, Map.of("title", "intruder")).getStatusCode().value()).isEqualTo(404);
        assertThat(call(HttpMethod.DELETE, "/api/conversations/" + id, bob, null).getStatusCode().value()).isEqualTo(404);
        assertThat(call(HttpMethod.GET, "/api/v1/conversations/" + id + "/tasks", bob, null).getStatusCode().value()).isEqualTo(404);
        for (String route : List.of("/api/chat", "/api/chat/stream")) {
            assertThat(call(HttpMethod.POST, route, bob, Map.of("conversation_id", bId, "session_id", id, "messages", List.of(Map.of("role", "user", "content", "test")))).getStatusCode().value()).isEqualTo(404);
        }
        for (String route : List.of("/api/v1/recommend", "/api/v1/recommend/stream"))
            assertThat(call(HttpMethod.POST, route, bob, Map.of("conversation_id", id, "query", "test")).getStatusCode().value()).isEqualTo(404);
        assertThat(CALLS).hasSize(before);
        assertThat(call(HttpMethod.GET, "/api/conversations", bob, null).getBody()).contains(bId).doesNotContain(id);
        assertThat(call(HttpMethod.GET, "/api/session-state/" + id, alice, null).getStatusCode().value()).isEqualTo(200);
        assertThat(call(HttpMethod.GET, "/api/session-state/" + UUID.randomUUID(), alice, null).getStatusCode().value()).isEqualTo(404);
    }

    @Test void taskReadsAndConfirmAliasesArePrivate() throws Exception {
        String alice = login("alice"), bob = login("bob"), id = conversation(alice);
        var result = call(HttpMethod.POST, "/api/v1/recommend", alice, Map.of("conversation_id", id, "query", "needs confirmation"));
        assertThat(result.getStatusCode().value()).isEqualTo(200);
        String task = json(result).path("task_id").asText();
        int before = CALLS.size();
        for (String path : List.of("/api/v1/tasks/" + task, "/api/v1/recommend/task/" + task))
            assertThat(call(HttpMethod.GET, path, bob, null).getStatusCode().value()).isEqualTo(404);
        for (String path : List.of("/api/v1/recommend/confirm?taskId=" + task, "/api/v1/recommend/confirm/stream?taskId=" + task + "&action=edit"))
            assertThat(call(HttpMethod.POST, path, bob, Map.of("sub_questions", List.of("hack"))).getStatusCode().value()).isEqualTo(404);
        for (String reference : List.of(id, task))
            for (String path : List.of("/api/chat/confirm", "/api/chat/confirm/stream"))
                assertThat(call(HttpMethod.POST, path, bob, Map.of("session_id", reference)).getStatusCode().value()).isEqualTo(404);
        assertThat(CALLS).hasSize(before);
    }

    @Test void streamsPersistsAndResumesOwnedTasksWithServiceCredential() throws Exception {
        String alice = login("alice"), bob = login("bob"), id = conversation(alice);
        var stream = call(HttpMethod.POST, "/api/chat/stream", alice, Map.of("conversation_id", id,
                "messages", List.of(Map.of("role", "user", "content", "needs confirmation"))));
        assertThat(stream.getStatusCode().value()).isEqualTo(200);
        assertThat(stream.getBody()).contains("event:awaiting_confirmation");
        String task = json(call(HttpMethod.GET, "/api/v1/conversations/" + id + "/tasks", alice, null)).get(0).path("task_id").asText();
        assertThat(call(HttpMethod.POST, "/api/chat/confirm/stream", bob, Map.of("session_id", task)).getStatusCode().value()).isEqualTo(404);
        var resume = call(HttpMethod.POST, "/api/chat/confirm/stream", alice,
                Map.of("session_id", task, "conversation_id", id, "action", "edit", "sub_questions", List.of("Postgres?")));
        assertThat(resume.getStatusCode().value()).isEqualTo(200);
        assertThat(resume.getBody()).contains("event:token", "Hello ", "world", "event:final");
        StringBuilder text = new StringBuilder();
        for (String frame : resume.getBody().split("\n\n")) {
            for (String line : frame.split("\n")) {
                if (!line.startsWith("data:")) continue;
                JsonNode event = JSON.readTree(line.substring(5));
                if (event.path("type").asText().equals("token")) text.append(event.path("delta").asText());
            }
        }
        assertThat(text.toString()).isEqualTo("Hello world");
        assertThat(json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null)).path("status").asText()).isEqualTo("DONE");
        assertThat(call(HttpMethod.GET, "/api/session-state/" + id, alice, null).getBody()).contains("Hello world");
        assertThat(call(HttpMethod.GET, "/api/v1/assets/test.png", alice, null).getStatusCode().value()).isEqualTo(200);
        assertThat(KEYS).isNotEmpty().allMatch("test-agent-key"::equals);
    }

    @Test void legacyAndDeletedConversationsCannotBeClaimed() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        var legacy = new com.example.demo.conversation.entity.ConversationEntity();
        legacy.setTitle("Legacy private history"); conversations.saveAndFlush(legacy);
        assertThat(call(HttpMethod.GET, "/api/session-state/" + legacy.getId(), alice, null).getStatusCode().value()).isEqualTo(404);
        assertThat(call(HttpMethod.GET, "/api/conversations", alice, null).getBody()).doesNotContain(legacy.getId());
        assertThat(call(HttpMethod.DELETE, "/api/conversations/" + id, alice, null).getStatusCode().value()).isEqualTo(200);
        assertThat(call(HttpMethod.PUT, "/api/session-state/" + id, alice, Map.of("facts", Map.of())).getStatusCode().value()).isEqualTo(404);
        assertThat(conversations.findById(legacy.getId()).orElseThrow().getOwnerUserId()).isNull();
    }

    @Test void unresolvedRoutingDoesNotWriteOrStartTasks() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        long conversationCount = conversations.count(), taskCount = tasks.count(), messageCount = messages.count();
        int calls = CALLS.size();
        for (String route : List.of("/api/chat", "/api/chat/stream")) {
            var response = call(HttpMethod.POST, route, alice, Map.of("routing_mode", "auto", "current_conversation_id", id,
                    "messages", List.of(Map.of("role", "user", "content", "continue or start something else"))));
            assertThat(response.getStatusCode().value()).isEqualTo(200);
            assertThat(response.getBody()).contains("routing_clarification", id);
        }
        for (String route : List.of("/api/v1/recommend", "/api/v1/recommend/stream")) {
            var response = call(HttpMethod.POST, route, alice,
                    Map.of("routing_mode", "auto", "current_conversation_id", id, "query", "ambiguous"));
            assertThat(response.getStatusCode().value()).isEqualTo(200);
            assertThat(response.getBody()).contains("routing_clarification", id);
        }
        assertThat(conversations.count()).isEqualTo(conversationCount);
        assertThat(tasks.count()).isEqualTo(taskCount);
        assertThat(messages.count()).isEqualTo(messageCount);
        assertThat(CALLS).hasSize(calls);
    }

    @Test void structuredThreadWinsAndConflictingNewIsRejectedBeforeWrites() throws Exception {
        String alice = login("alice"), bob = login("bob"), id = conversation(alice);
        long taskCount = tasks.count(); int calls = CALLS.size();
        assertThat(call(HttpMethod.POST, "/api/v1/recommend", alice,
                Map.of("routing_mode", "new", "thread_id", id, "query", "test")).getStatusCode().value()).isEqualTo(400);
        assertThat(call(HttpMethod.POST, "/api/v1/recommend", bob,
                Map.of("routing_mode", "auto", "thread_id", id, "query", "test")).getStatusCode().value()).isEqualTo(404);
        assertThat(tasks.count()).isEqualTo(taskCount); assertThat(CALLS).hasSize(calls);
        var response = call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                Map.of("routing_mode", "auto", "thread_id", id, "query", "test"));
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).contains("event:final", id).doesNotContain("routing_clarification");
    }

    @Test void waitingInputPersistsBlocksOverwriteAndResumesExactTask() throws Exception {
        String alice = login("alice"), bob = login("bob"), id = conversation(alice);
        var created = call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                Map.of("conversation_id", id, "query", "needs input"));
        assertThat(created.getBody()).contains("event:awaiting_input", "input-1");
        String task = json(call(HttpMethod.GET, "/api/v1/conversations/" + id + "/tasks", alice, null)).get(0).path("task_id").asText();
        var state = json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null));
        assertThat(state.path("status").asText()).isEqualTo("PENDING_INPUT");
        assertThat(state.path("pending_input").path("clarification_id").asText()).isEqualTo("input-1");
        int calls = CALLS.size(); long count = messages.count();
        assertThat(call(HttpMethod.POST, "/api/chat/stream", alice, Map.of("conversation_id", id,
                "messages", List.of(Map.of("role", "user", "content", "overwrite")))).getStatusCode().value()).isEqualTo(409);
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/clarify", bob,
                Map.of("task_id", task, "clarification_id", "input-1", "answer", "local")).getStatusCode().value()).isEqualTo(404);
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/clarify", alice,
                Map.of("task_id", task, "clarification_id", "stale", "answer", "local")).getStatusCode().value()).isEqualTo(409);
        assertThat(CALLS).hasSize(calls); assertThat(messages.count()).isEqualTo(count);
        var resumed = call(HttpMethod.POST, "/api/v1/recommend/clarify/stream", alice,
                Map.of("task_id", task, "clarification_id", "input-1", "answer", "local"));
        assertThat(resumed.getStatusCode().value()).isEqualTo(200);
        assertThat(resumed.getBody()).contains("event:final", task);
        assertThat(json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null)).path("status").asText()).isEqualTo("DONE");
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/clarify", alice,
                Map.of("task_id", task, "clarification_id", "input-1", "answer", "duplicate")).getStatusCode().value()).isEqualTo(409);
    }

    @Test void cancelWaitingInputRejectsOldResumeAndAllowsNewTask() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        var created = call(HttpMethod.POST, "/api/v1/recommend", alice, Map.of("conversation_id", id, "query", "needs input"));
        assertThat(json(created).path("status").asText()).isEqualTo("PENDING_INPUT");
        String task = json(created).path("task_id").asText();
        var cancelled = call(HttpMethod.POST, "/api/v1/recommend/tasks/" + task + "/cancel", alice, Map.of());
        assertThat(json(cancelled).path("status").asText()).isEqualTo("CANCELLED");
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/clarify", alice,
                Map.of("task_id", task, "clarification_id", "input-1", "answer", "late")).getStatusCode().value()).isEqualTo(409);
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                Map.of("conversation_id", id, "query", "test")).getBody()).contains("event:final");
    }

    @Test void rejectedClarificationBeforeExecutionKeepsWaitingAndRetriesWithoutDuplicateAnswers() throws Exception {
        String alice = login("alice");
        for (String suffix : List.of("", "/stream")) {
            for (int rejection : List.of(409, 422, 503)) {
                String id = conversation(alice);
                var created = json(call(HttpMethod.POST, "/api/v1/recommend", alice,
                        Map.of("conversation_id", id, "query", "needs input")));
                String task = created.path("task_id").asText();
                Map<String, String> answer = Map.of("task_id", task, "clarification_id", "input-1", "answer", "Linux");
                long before = messages.count();
                CLARIFICATION_REJECTIONS.put(id, rejection);
                var rejected = call(HttpMethod.POST, "/api/v1/recommend/clarify" + suffix, alice, answer);
                if (suffix.isEmpty()) assertThat(rejected.getStatusCode().value()).isEqualTo(502);
                else assertThat(rejected.getBody()).contains("event:error").doesNotContain("event:meta");
                var waiting = json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null));
                assertThat(waiting.path("status").asText()).isEqualTo("PENDING_INPUT");
                assertThat(waiting.path("pending_input").path("clarification_id").asText()).isEqualTo("input-1");
                assertThat(messages.count()).isEqualTo(before);
                assertThat(call(HttpMethod.POST, "/api/v1/recommend", alice,
                        Map.of("conversation_id", id, "query", "overwrite")).getStatusCode().value()).isEqualTo(409);
                var retried = call(HttpMethod.POST, "/api/v1/recommend/clarify" + suffix, alice, answer);
                assertThat(retried.getStatusCode().value()).isEqualTo(200);
                assertThat(json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null)).path("status").asText()).isEqualTo("DONE");
                assertThat(messages.findByConversation_IdOrderByCreatedAtDesc(id, org.springframework.data.domain.PageRequest.of(0, 50))
                        .getContent().stream().filter(message -> "user".equals(message.getRole()) && "Linux".equals(message.getContent())).count()).isEqualTo(1);
            }
        }
    }

    @Test void disconnectedResumeAndOversizedAnswersKeepTheTaskCancellable() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        String task = json(call(HttpMethod.POST, "/api/v1/recommend", alice,
                Map.of("conversation_id", id, "query", "needs input"))).path("task_id").asText();
        long count = messages.count(); int calls = CALLS.size();
        for (String suffix : List.of("", "/stream")) {
            assertThat(call(HttpMethod.POST, "/api/v1/recommend/clarify" + suffix, alice,
                    Map.of("task_id", task, "clarification_id", "input-1", "answer", "x".repeat(4001))).getStatusCode().value()).isEqualTo(400);
        }
        assertThat(CALLS).hasSize(calls); assertThat(messages.count()).isEqualTo(count);
        CLARIFICATION_REJECTIONS.put(id, 0);
        var disconnected = call(HttpMethod.POST, "/api/v1/recommend/clarify/stream", alice,
                Map.of("task_id", task, "clarification_id", "input-1", "answer", "Linux"));
        assertThat(disconnected.getBody()).contains("event:error").doesNotContain("event:meta");
        assertThat(messages.count()).isEqualTo(count);
        assertThat(json(call(HttpMethod.POST, "/api/v1/recommend/tasks/" + task + "/cancel", alice, Map.of()))
                .path("status").asText()).isEqualTo("CANCELLED");
    }

    @Test void staleResumeFailureCannotReleaseTheNextReservation() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        String task = json(call(HttpMethod.POST, "/api/v1/recommend", alice,
                Map.of("conversation_id", id, "query", "needs input"))).path("task_id").asText();
        var first = taskStates.prepareClarification(task, "input-1", "Linux");
        assertThat(taskStates.restoreClarification(first)).isTrue();
        var second = taskStates.prepareClarification(task, "input-1", "Windows");
        assertThat(taskStates.restoreClarification(first)).isFalse();
        assertThat(taskStates.acceptClarification(first)).isFalse();
        assertThat(json(call(HttpMethod.GET, "/api/v1/tasks/" + task, alice, null)).path("status").asText()).isEqualTo("GENERATING");
        assertThat(taskStates.restoreClarification(second)).isTrue();
        assertThat(json(call(HttpMethod.POST, "/api/v1/recommend/tasks/" + task + "/cancel", alice, Map.of()))
                .path("status").asText()).isEqualTo("CANCELLED");
    }

    @Test void incompleteAnswerRemainsTerminalAndKeepsPartialResult() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        var created = json(call(HttpMethod.POST, "/api/v1/recommend", alice,
                Map.of("conversation_id", id, "query", "partial evidence")));
        assertThat(created.path("status").asText()).isEqualTo("INCOMPLETE");
        assertThat(created.path("final_result").asText()).contains("Only one requirement");
        var refreshed = json(call(HttpMethod.GET, "/api/v1/tasks/" + created.path("task_id").asText(), alice, null));
        assertThat(refreshed.path("status").asText()).isEqualTo("INCOMPLETE");
        assertThat(refreshed.path("final_result").asText()).contains("Only one requirement");
        assertThat(call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                Map.of("conversation_id", id, "query", "test")).getStatusCode().value()).isEqualTo(200);
    }

    @Test void concurrentMessagesCannotReserveTheSameConversationThread() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        var pool = java.util.concurrent.Executors.newFixedThreadPool(2);
        var start = new java.util.concurrent.CountDownLatch(1);
        try {
            java.util.concurrent.Callable<Integer> submit = () -> {
                start.await();
                return call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                        Map.of("conversation_id", id, "query", "needs input")).getStatusCode().value();
            };
            var first = pool.submit(submit); var second = pool.submit(submit); start.countDown();
            assertThat(List.of(first.get(20, java.util.concurrent.TimeUnit.SECONDS), second.get(20, java.util.concurrent.TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder(200, 409);
            assertThat(tasks.findByConversation_IdOrderByCreatedAtDesc(id)).hasSize(1);
        } finally { pool.shutdownNow(); }
    }

    @Test void preflightAndHealthRemainAvailable() {
        HttpHeaders headers = new HttpHeaders();
        headers.set("Origin", "http://localhost:3000"); headers.set("Access-Control-Request-Method", "POST");
        headers.set("Access-Control-Request-Headers", "authorization,content-type");
        var response = http.exchange("/api/chat/stream", HttpMethod.OPTIONS, new HttpEntity<>(headers), String.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getHeaders().getAccessControlAllowOrigin()).isEqualTo("http://localhost:3000");
        assertThat(call(HttpMethod.GET, "/actuator/health", null, null).getStatusCode().value()).isEqualTo(200);
    }

    @Test void v1StreamAndLocalMemoryShortcutFinishTheirTasks() throws Exception {
        String alice = login("alice"), id = conversation(alice);
        var stream = call(HttpMethod.POST, "/api/v1/recommend/stream", alice,
                Map.of("conversation_id", id, "query", "test"));
        assertThat(stream.getStatusCode().value()).isEqualTo(200);
        assertThat(stream.getBody()).contains("\"delta\":\" \"", "event:final");
        var tasks = json(call(HttpMethod.GET, "/api/v1/conversations/" + id + "/tasks", alice, null));
        assertThat(tasks.get(0).path("status").asText()).isEqualTo("DONE");
        call(HttpMethod.PUT, "/api/session-state/" + id, alice, Map.of("facts", Map.of("user_name", "Alice")));
        int before = CALLS.size();
        var local = call(HttpMethod.POST, "/api/chat/stream", alice,
                Map.of("conversation_id", id, "messages", List.of(Map.of("role", "user", "content", "what is my name?"))));
        assertThat(local.getBody()).contains("event:final", "Alice");
        assertThat(CALLS).hasSize(before);
        tasks = json(call(HttpMethod.GET, "/api/v1/conversations/" + id + "/tasks", alice, null));
        assertThat(tasks.get(0).path("status").asText()).isEqualTo("DONE");
    }
}

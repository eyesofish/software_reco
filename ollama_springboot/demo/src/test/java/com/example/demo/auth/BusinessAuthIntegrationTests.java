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
    static final HttpServer AGENT = createAgent();
    @Autowired TestRestTemplate http;
    @Autowired JdbcTemplate database;
    @Autowired UserRepository users;
    @Autowired JwtEncoder encoder;
    @Autowired com.example.demo.conversation.repository.ConversationRepository conversations;
    @Autowired com.example.demo.conversation.repository.RecommendTaskRepository tasks;
    @Autowired com.example.demo.conversation.repository.ConversationMessageRepository messages;

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
                String result;
                if (path.contains("assets")) {
                    byte[] image = Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jk1sAAAAASUVORK5CYII=");
                    exchange.getResponseHeaders().set("Content-Type", "image/png");
                    exchange.sendResponseHeaders(200, image.length);
                    exchange.getResponseBody().write(image); exchange.close(); return;
                } else if (path.endsWith("stream")) {
                    boolean pending = !path.contains("confirm") && request.path("query").asText().contains("needs confirmation");
                    result = "event: meta\ndata: {\"type\":\"meta\",\"session_id\":\"" + session + "\"}\n\n";
                    if (pending) result += "event: awaiting_confirmation\ndata: {\"type\":\"awaiting_confirmation\",\"sub_questions\":[\"Which database?\"]}\n\n";
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

    @Test void preflightAndHealthRemainAvailable() {
        HttpHeaders headers = new HttpHeaders();
        headers.set("Origin", "http://localhost:3000"); headers.set("Access-Control-Request-Method", "POST");
        headers.set("Access-Control-Request-Headers", "authorization,content-type");
        var response = http.exchange("/api/chat/stream", HttpMethod.OPTIONS, new HttpEntity<>(headers), String.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getHeaders().getAccessControlAllowOrigin()).isEqualTo("http://localhost:3000");
        assertThat(call(HttpMethod.GET, "/actuator/health", null, null).getStatusCode().value()).isEqualTo(200);
    }

}

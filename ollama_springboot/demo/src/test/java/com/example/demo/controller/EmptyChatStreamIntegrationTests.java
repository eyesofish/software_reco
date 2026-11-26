package com.example.demo.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "spring.datasource.url=${CHAT_TEST_DB_URL:jdbc:h2:mem:empty_chat_stream;DB_CLOSE_DELAY=-1}",
        "app.routing.mode=off"
})
class EmptyChatStreamIntegrationTests {
    private static HttpServer agent;

    @LocalServerPort int port;
    @Autowired JdbcTemplate database;
    @Value("${app.auth.alice-password:}") String alicePassword;

    @DynamicPropertySource
    static void startAgent(DynamicPropertyRegistry properties) throws IOException {
        agent = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        agent.createContext("/api/v1/session-state/", exchange -> {
            try (exchange) {
                exchange.sendResponseHeaders(204, -1);
            }
        });
        agent.createContext("/api/v1/recommend/stream", exchange -> {
            try (exchange) {
                byte[] emptySuccess = """
                        event: final
                        data: {"type":"final","status":"success","final_answer":"   "}

                        """.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
                exchange.sendResponseHeaders(200, emptySuccess.length);
                exchange.getResponseBody().write(emptySuccess);
            }
        });
        agent.start();
        properties.add("app.fastapi.base-url", () -> "http://127.0.0.1:" + agent.getAddress().getPort());
    }

    @AfterAll
    static void stopAgent() {
        if (agent != null) agent.stop(0);
    }

    @Test
    void emptySuccessWithoutTokensFailsTheTaskAndReportsAnErrorToTheClient() {
        Duration timeout = Duration.ofSeconds(15);
        WebClient client = WebClient.create("http://127.0.0.1:" + port);
        var request = client.post().uri("/api/chat/stream").contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM).header("X-Request-Id", UUID.randomUUID().toString());
        if (!alicePassword.isBlank()) {
            JsonNode login = client.post().uri("/api/auth/login")
                    .bodyValue(Map.of("username", "alice", "password", alicePassword))
                    .retrieve().bodyToMono(JsonNode.class).block(timeout);
            request.headers(headers -> headers.setBearerAuth(login.required("access_token").asText()));
        }
        var events = request.bodyValue(Map.of("messages", List.of(Map.of("role", "user", "content", "Help me pick a database"))))
                .retrieve().bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<JsonNode>>() {})
                .collectList().block(timeout);

        assertThat(events).extracting(ServerSentEvent::event).containsExactly("meta", "error");
        JsonNode error = events.getLast().data();
        assertThat(error.required("type").asText()).isEqualTo("error");
        assertThat(error.required("message").asText()).isNotBlank();
        String taskId = error.required("task_id").asText();
        String conversationId = error.required("conversation_id").asText();
        var task = database.queryForMap(
                "SELECT status, error_message FROM recommend_tasks WHERE id = ? AND conversation_id = ?", taskId, conversationId);
        assertThat(task.get("status")).isEqualTo("FAILED");
        assertThat((String) task.get("error_message")).containsIgnoringCase("empty");
        assertThat(database.queryForObject(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ? AND role = 'assistant'",
                Long.class, conversationId)).isZero();
    }
}

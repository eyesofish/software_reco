package com.example.demo.conversation.service;

import com.example.demo.dto.Message;
import com.example.demo.dto.OllamaChatRequest;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

class ChatPreparationTests {
    private final ChatSubmissionService submissions = new ChatSubmissionService(null, null, null);

    @Test
    void sameKeySharesPreparationFailureThenCanBeRetried() throws Exception {
        String id = UUID.randomUUID().toString();
        var key = submissions.key(id, message("first"));
        var changed = submissions.key(id, message("changed"));
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        var failure = new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE);
        var leader = new FutureTask<>(() -> submissions.prepareOnce("owner", key, String.class, () -> {
            entered.countDown();
            try {
                if (!release.await(10, TimeUnit.SECONDS)) throw new AssertionError("Leader was never released");
            } catch (InterruptedException interrupted) {
                throw new AssertionError(interrupted);
            }
            throw failure;
        }));
        Thread.ofVirtual().start(leader);
        try {
            assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();
            assertThatThrownBy(() -> submissions.prepareOnce("owner", changed, String.class, () -> "wrong"))
                    .isInstanceOfSatisfying(ResponseStatusException.class,
                            error -> assertThat(error.getStatusCode()).isEqualTo(HttpStatus.CONFLICT));
            var follower = new FutureTask<>(() -> submissions.prepareOnce("owner", key, String.class,
                    () -> { throw new AssertionError("Second caller executed the preflight"); }));
            var thread = Thread.ofVirtual().start(follower);
            await().atMost(5, TimeUnit.SECONDS).until(() -> thread.getState() == Thread.State.WAITING);
            release.countDown();
            for (var result : List.of(leader, follower)) {
                assertThatThrownBy(() -> result.get(5, TimeUnit.SECONDS))
                        .isInstanceOf(ExecutionException.class).hasCause(failure);
            }
            assertThat(submissions.prepareOnce("owner", key, String.class, () -> "ready").value()).isEqualTo("ready");
        } finally {
            release.countDown();
        }
    }

    private OllamaChatRequest message(String text) {
        var message = new Message();
        message.setRole("user");
        message.setContent(text);
        var request = new OllamaChatRequest();
        request.setMessages(List.of(message));
        return request;
    }
}

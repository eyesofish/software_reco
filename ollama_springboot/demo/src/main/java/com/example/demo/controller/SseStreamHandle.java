package com.example.demo.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.Disposable;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Bridges an SSE response to a cancellable upstream subscription. */
final class SseStreamHandle {
    private static final Logger logger = LoggerFactory.getLogger(SseStreamHandle.class);
    private static final ScheduledExecutorService HEARTBEATS = Executors.newSingleThreadScheduledExecutor(task -> {
        Thread thread = new Thread(task, "sse-heartbeat");
        thread.setDaemon(true);
        return thread;
    });

    private final SseEmitter emitter;
    private final Runnable onDisconnect;
    private final AtomicBoolean terminal = new AtomicBoolean();
    private final AtomicReference<Disposable> upstream = new AtomicReference<>();
    private final ScheduledFuture<?> heartbeat;

    private SseStreamHandle(SseEmitter emitter, Runnable onDisconnect) {
        this.emitter = emitter;
        this.onDisconnect = onDisconnect;
        this.heartbeat = HEARTBEATS.scheduleAtFixedRate(this::sendHeartbeat, 2, 2, TimeUnit.SECONDS);
        emitter.onCompletion(this::disconnectIfUnexpected);
        emitter.onTimeout(this::disconnect);
        emitter.onError(error -> disconnect());
    }

    static SseStreamHandle start(SseEmitter emitter, Runnable onDisconnect) {
        return new SseStreamHandle(emitter, onDisconnect);
    }

    void attach(Disposable disposable) {
        upstream.set(disposable);
        if (terminal.get()) {
            disposable.dispose();
        }
    }

    boolean send(String eventType, Map<String, Object> payload) {
        if (terminal.get()) {
            return false;
        }
        try {
            emitter.send(SseEmitter.event().name(eventType).data(payload, MediaType.APPLICATION_JSON));
            return true;
        } catch (IOException | IllegalStateException error) {
            disconnect();
            return false;
        }
    }

    void complete() {
        if (terminal.compareAndSet(false, true)) {
            cleanup();
            emitter.complete();
        }
    }

    void fail(Throwable error) {
        if (terminal.get()) {
            return;
        }
        String message = error == null ? "stream failed" : String.valueOf(error.getMessage());
        if (send("error", Map.of("type", "error", "message", message))) {
            complete();
        }
    }

    private void sendHeartbeat() {
        if (terminal.get()) {
            return;
        }
        try {
            emitter.send(SseEmitter.event().comment("keep-alive"));
        } catch (IOException | IllegalStateException error) {
            logger.debug("SSE heartbeat detected a disconnected client: {}", error.toString());
            disconnect();
        }
    }

    private void disconnectIfUnexpected() {
        if (!terminal.get()) {
            disconnect();
        }
    }

    private void disconnect() {
        if (terminal.compareAndSet(false, true)) {
            cleanup();
            onDisconnect.run();
        }
    }

    private void cleanup() {
        heartbeat.cancel(false);
        Disposable disposable = upstream.getAndSet(null);
        if (disposable != null) {
            disposable.dispose();
        }
    }
}

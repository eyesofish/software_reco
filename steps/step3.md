### 改动清单

1. SpringBoot 增加 `JPA + Flyway + PostgreSQL` 依赖。
2. 增加迁移脚本：`conversations`、`conversation_messages` 两张表。
3. 新增会话模块：实体、仓储、服务、控制器。
4. 新增会话 API：`GET/POST/PATCH/DELETE /api/conversations`。
5. 聊天链路透传会话标识到 FastAPI：`conversation_id -> session_id`。
6. FastAPI 模型兼容 `conversation_id`。

##### File: ollama_springboot/demo/pom.xml

```xml
<!-- 插入到 <dependencies> -->
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-data-jpa</artifactId>
</dependency>
<dependency>
  <groupId>org.flywaydb</groupId>
  <artifactId>flyway-core</artifactId>
</dependency>
<dependency>
  <groupId>org.postgresql</groupId>
  <artifactId>postgresql</artifactId>
  <scope>runtime</scope>
</dependency>
```

##### File: ollama_springboot/demo/src/main/resources/application.yml

```yaml
spring:
  jackson:
    property-naming-strategy: SNAKE_CASE
  datasource:
    url: ${DB_URL:jdbc:postgresql://localhost:5432/software_reco}
    username: ${DB_USER:postgres}
    password: ${DB_PASSWORD:postgres}
    driver-class-name: org.postgresql.Driver
  jpa:
    hibernate:
      ddl-auto: validate
    open-in-view: false
  flyway:
    enabled: true
```

##### File: ollama_springboot/demo/src/main/resources/db/migration/V1__create_conversation_tables.sql

```sql
CREATE TABLE conversations (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    preview VARCHAR(500),
    model_name VARCHAR(128),
    message_count INTEGER NOT NULL DEFAULT 0,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    last_message_role VARCHAR(16),
    last_message_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX idx_conversation_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_conversation_deleted_archived ON conversations(deleted, archived);

CREATE TABLE conversation_messages (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT fk_conversation_messages_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX idx_message_conversation_created_at
  ON conversation_messages(conversation_id, created_at ASC);
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/entity/ConversationEntity.java

```java
package com.example.demo.conversation.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.Instant;
import java.util.UUID;

@Getter
@Setter
@Entity
@Table(name = "conversations")
public class ConversationEntity {
    @Id
    @Column(length = 36, nullable = false, updatable = false)
    private String id;
    @Column(nullable = false, length = 200)
    private String title;
    @Column(length = 500)
    private String preview;
    @Column(name = "model_name", length = 128)
    private String modelName;
    @Column(name = "message_count", nullable = false)
    private int messageCount;
    @Column(nullable = false)
    private boolean archived;
    @Column(nullable = false)
    private boolean pinned;
    @Column(nullable = false)
    private boolean deleted;
    @Column(name = "last_message_role", length = 16)
    private String lastMessageRole;
    @Column(name = "last_message_at")
    private Instant lastMessageAt;
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    public void prePersist() {
        Instant now = Instant.now();
        if (id == null || id.isBlank()) id = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = now;
        if (updatedAt == null) updatedAt = now;
    }

    @PreUpdate
    public void preUpdate() {
        updatedAt = Instant.now();
    }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/entity/ConversationMessageEntity.java

```java
package com.example.demo.conversation.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.Instant;
import java.util.UUID;

@Getter
@Setter
@Entity
@Table(name = "conversation_messages")
public class ConversationMessageEntity {
    @Id
    @Column(length = 36, nullable = false, updatable = false)
    private String id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "conversation_id", nullable = false)
    private ConversationEntity conversation;

    @Column(nullable = false, length = 16)
    private String role;
    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @PrePersist
    public void prePersist() {
        if (id == null || id.isBlank()) id = UUID.randomUUID().toString();
        if (createdAt == null) createdAt = Instant.now();
    }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationRepository.java

```java
package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import java.util.Optional;

public interface ConversationRepository extends JpaRepository<ConversationEntity, String>, JpaSpecificationExecutor<ConversationEntity> {
    Optional<ConversationEntity> findByIdAndDeletedFalse(String id);
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationMessageRepository.java

```java
package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationMessageEntity;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessageEntity, String> {}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/dto/ConversationApiModels.java

```java
package com.example.demo.conversation.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class ConversationApiModels {
    private ConversationApiModels() {}

    public record CreateRequest(
            @Size(max = 200) String title,
            @NotNull @Valid FirstMessage firstMessage,
            @Size(max = 128) String model
    ) {
        public record FirstMessage(
                @NotBlank @Size(max = 16) String role,
                @NotBlank @Size(max = 4000) String content
        ) {}
    }

    public record UpdateRequest(@Size(max = 200) String title, Boolean archived, Boolean pinned) {
        public boolean hasAnyField() { return title != null || archived != null || pinned != null; }
    }

    public record ListItem(
            String id, String title, String preview, int messageCount,
            String lastMessageRole, Instant lastMessageAt, Instant updatedAt, Instant createdAt, boolean archived
    ) {}
    public record Pagination(int page, int pageSize, long total, int totalPages, boolean hasNext, boolean hasPrev) {}
    public record ListResponse(List<ListItem> items, Pagination pagination) {}

    public record MessageItem(String id, String role, String content, Instant createdAt) {}
    public record CreateResponse(String id, String title, Instant createdAt, Instant updatedAt, int messageCount, List<MessageItem> messages) {}
    public record UpdateResponse(String id, String title, boolean archived, boolean pinned, Instant updatedAt) {}
    public record DeleteResponse(String id, boolean deleted, boolean hard, Instant deletedAt) {}
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/service/ConversationService.java

```java
package com.example.demo.conversation.service;

import com.example.demo.conversation.dto.ConversationApiModels;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.entity.ConversationMessageEntity;
import com.example.demo.conversation.repository.ConversationMessageRepository;
import com.example.demo.conversation.repository.ConversationRepository;
import jakarta.persistence.criteria.Predicate;
import org.springframework.data.domain.*;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

@Service
public class ConversationService {
    private final ConversationRepository conversationRepository;
    private final ConversationMessageRepository messageRepository;

    public ConversationService(ConversationRepository conversationRepository, ConversationMessageRepository messageRepository) {
        this.conversationRepository = conversationRepository;
        this.messageRepository = messageRepository;
    }

    @Transactional(readOnly = true)
    public ConversationApiModels.ListResponse list(int page, int pageSize, String q, boolean includeArchived, Sort sort) {
        Pageable pageable = PageRequest.of(Math.max(page - 1, 0), Math.max(1, Math.min(pageSize, 100)), sort);
        Specification<ConversationEntity> spec = (root, query, cb) -> {
            List<Predicate> ps = new ArrayList<>();
            ps.add(cb.isFalse(root.get("deleted")));
            if (!includeArchived) ps.add(cb.isFalse(root.get("archived")));
            if (q != null && !q.isBlank()) {
                String like = "%" + q.trim().toLowerCase(Locale.ROOT) + "%";
                ps.add(cb.or(cb.like(cb.lower(root.get("title")), like), cb.like(cb.lower(root.get("preview")), like)));
            }
            return cb.and(ps.toArray(new Predicate[0]));
        };

        Page<ConversationEntity> p = conversationRepository.findAll(spec, pageable);
        List<ConversationApiModels.ListItem> items = p.getContent().stream().map(c ->
                new ConversationApiModels.ListItem(
                        c.getId(), c.getTitle(), c.getPreview(), c.getMessageCount(),
                        c.getLastMessageRole(), c.getLastMessageAt(), c.getUpdatedAt(), c.getCreatedAt(), c.isArchived()
                )).toList();
        return new ConversationApiModels.ListResponse(
                items,
                new ConversationApiModels.Pagination(p.getNumber() + 1, p.getSize(), p.getTotalElements(), p.getTotalPages(), p.hasNext(), p.hasPrevious())
        );
    }

    @Transactional
    public ConversationApiModels.CreateResponse create(ConversationApiModels.CreateRequest request) {
        String role = normalizeRole(request.firstMessage().role());
        if (!"user".equals(role)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "first_message.role must be user");
        String content = request.firstMessage().content().trim();

        ConversationEntity c = new ConversationEntity();
        c.setTitle(resolveTitle(request.title(), content));
        c.setPreview(content.length() > 500 ? content.substring(0, 500) : content);
        c.setModelName(trimToNull(request.model()));
        c = conversationRepository.save(c);

        ConversationMessageEntity m = new ConversationMessageEntity();
        m.setConversation(c);
        m.setRole(role);
        m.setContent(content);
        m = messageRepository.save(m);

        c.setMessageCount(1);
        c.setLastMessageRole(m.getRole());
        c.setLastMessageAt(m.getCreatedAt());
        c.setUpdatedAt(m.getCreatedAt());
        c = conversationRepository.save(c);

        return new ConversationApiModels.CreateResponse(
                c.getId(), c.getTitle(), c.getCreatedAt(), c.getUpdatedAt(), c.getMessageCount(),
                List.of(new ConversationApiModels.MessageItem(m.getId(), m.getRole(), m.getContent(), m.getCreatedAt()))
        );
    }

    @Transactional
    public ConversationApiModels.UpdateResponse patch(String id, ConversationApiModels.UpdateRequest request) {
        if (!request.hasAnyField()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least one field is required");
        ConversationEntity c = conversationRepository.findByIdAndDeletedFalse(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Conversation not found"));
        if (request.title() != null) c.setTitle(resolveTitle(request.title(), c.getPreview()));
        if (request.archived() != null) c.setArchived(request.archived());
        if (request.pinned() != null) c.setPinned(request.pinned());
        c = conversationRepository.save(c);
        return new ConversationApiModels.UpdateResponse(c.getId(), c.getTitle(), c.isArchived(), c.isPinned(), c.getUpdatedAt());
    }

    @Transactional
    public ConversationApiModels.DeleteResponse delete(String id, boolean hard) {
        ConversationEntity c = conversationRepository.findByIdAndDeletedFalse(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Conversation not found"));
        Instant now = Instant.now();
        if (hard) {
            conversationRepository.delete(c);
        } else {
            c.setDeleted(true);
            c.setArchived(true);
            c.setUpdatedAt(now);
            conversationRepository.save(c);
        }
        return new ConversationApiModels.DeleteResponse(id, true, hard, now);
    }

    private String normalizeRole(String role) {
        String v = trimToNull(role);
        if (v == null) return "user";
        v = v.toLowerCase(Locale.ROOT);
        if (!"user".equals(v) && !"assistant".equals(v)) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid role");
        return v;
    }

    private String resolveTitle(String title, String fallback) {
        String t = trimToNull(title);
        if (t == null) t = trimToNull(fallback);
        if (t == null) t = "New Conversation";
        return t.length() > 200 ? t.substring(0, 200) : t;
    }

    private String trimToNull(String s) {
        if (s == null) return null;
        String t = s.trim();
        return t.isEmpty() ? null : t;
    }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/conversation/controller/ConversationController.java

```java
package com.example.demo.conversation.controller;

import com.example.demo.conversation.dto.ConversationApiModels;
import com.example.demo.conversation.service.ConversationService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.data.domain.Sort;
import org.springframework.http.HttpStatus;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

@Validated
@RestController
@RequestMapping("/api/conversations")
public class ConversationController {
    private final ConversationService conversationService;

    public ConversationController(ConversationService conversationService) {
        this.conversationService = conversationService;
    }

    @GetMapping
    public ConversationApiModels.ListResponse list(
            @RequestParam(defaultValue = "1") @Min(1) int page,
            @RequestParam(name = "page_size", defaultValue = "20") @Min(1) @Max(100) int pageSize,
            @RequestParam(required = false) String q,
            @RequestParam(name = "sort", defaultValue = "updated_at:desc") String sort,
            @RequestParam(name = "include_archived", defaultValue = "false") boolean includeArchived
    ) {
        return conversationService.list(page, pageSize, q, includeArchived, parseSort(sort));
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ConversationApiModels.CreateResponse create(@Valid @RequestBody ConversationApiModels.CreateRequest request) {
        return conversationService.create(request);
    }

    @PatchMapping("/{id}")
    public ConversationApiModels.UpdateResponse patch(@PathVariable String id, @Valid @RequestBody ConversationApiModels.UpdateRequest request) {
        return conversationService.patch(id, request);
    }

    @DeleteMapping("/{id}")
    public ConversationApiModels.DeleteResponse delete(@PathVariable String id, @RequestParam(defaultValue = "false") boolean hard) {
        return conversationService.delete(id, hard);
    }

    private Sort parseSort(String sort) {
        String[] p = (sort == null ? "updated_at:desc" : sort).split(":", 2);
        String field = switch (p[0]) {
            case "created_at" -> "createdAt";
            case "last_message_at" -> "lastMessageAt";
            default -> "updatedAt";
        };
        Sort.Direction d = (p.length > 1 && "asc".equalsIgnoreCase(p[1])) ? Sort.Direction.ASC : Sort.Direction.DESC;
        return Sort.by(d, field);
    }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatRequest.java

```java
package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

public class OllamaChatRequest {
    private String model;
    private List<Message> messages;
    private Boolean stream;

    @JsonProperty("conversation_id")
    private String conversationId;

    public String getModel() { return model; }
    public void setModel(String model) { this.model = model; }
    public List<Message> getMessages() { return messages; }
    public void setMessages(List<Message> messages) { this.messages = messages; }
    public Boolean getStream() { return stream; }
    public void setStream(Boolean stream) { this.stream = stream; }
    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/dto/RecommendRequest.java

```java
package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class RecommendRequest {
    private String query;
    private Integer timeout;
    @JsonProperty("max_iterations")
    private Integer maxIterations;
    @JsonProperty("session_id")
    private String sessionId;

    public RecommendRequest() {}
    public RecommendRequest(String query, Integer timeout, Integer maxIterations, String sessionId) {
        this.query = query;
        this.timeout = timeout;
        this.maxIterations = maxIterations;
        this.sessionId = sessionId;
    }

    public String getQuery() { return query; }
    public void setQuery(String query) { this.query = query; }
    public Integer getTimeout() { return timeout; }
    public void setTimeout(Integer timeout) { this.timeout = timeout; }
    public Integer getMaxIterations() { return maxIterations; }
    public void setMaxIterations(Integer maxIterations) { this.maxIterations = maxIterations; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java

```java
package com.example.demo.client;

import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class FastApiClient {
    private final RestTemplate restTemplate;
    private final String recommendUrl;

    public FastApiClient(RestTemplate restTemplate,
                         @Value("${app.fastapi.base-url}") String baseUrl,
                         @Value("${app.fastapi.recommend-path}") String recommendPath) {
        this.restTemplate = restTemplate;
        this.recommendUrl = baseUrl + recommendPath;
    }

    public RecommendResponse recommend(RecommendRequest request) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        Map<String, Object> payload = new HashMap<>();
        payload.put("query", request.getQuery());
        payload.put("timeout", request.getTimeout());
        payload.put("max_iterations", request.getMaxIterations());
        if (request.getSessionId() != null && !request.getSessionId().isBlank()) {
            payload.put("session_id", request.getSessionId());
        }

        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);
        ResponseEntity<RecommendResponse> response = restTemplate.exchange(
                recommendUrl, HttpMethod.POST, entity, RecommendResponse.class);
        return response.getBody();
    }
}
```

##### File: ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java

```java
// 关键改动：把 conversation_id 透传为 session_id
RecommendResponse fastapi = fastApiClient.recommend(
    new RecommendRequest(query, 60, 3, request.getConversationId())
);
```

##### File: fastapi_demo/app/api/v1/models.py

```python
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class RecommendationRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    timeout: int = Field(default=60, ge=10, le=300)
    max_iterations: int = Field(default=3, ge=1, le=10)
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None

    @model_validator(mode="after")
    def normalize_ids(self):
        if not self.session_id and self.conversation_id:
            self.session_id = self.conversation_id
        return self


class RecommendationConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    action: str = Field(default="confirm", pattern="^(confirm|edit)$")
    sub_questions: Optional[List[str]] = None
    comment: Optional[str] = None
    conversation_id: Optional[str] = None

    @model_validator(mode="after")
    def normalize_ids(self):
        if (not self.session_id) and self.conversation_id:
            self.session_id = self.conversation_id
        return self


class RecommendationResponse(BaseModel):
    status: str
    final_answer: str
    candidates: Optional[List[Dict[str, Any]]] = None
    mode: Optional[str] = None
    iteration_count: Optional[int] = None
    coverage: Optional[float] = None
    session_id: Optional[str] = None
    awaiting_human_confirmation: Optional[bool] = None
    pending_sub_questions: Optional[List[str]] = None
```

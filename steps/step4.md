File: D:\Github\software_reco\fastapi_demo\app\api\v1\models.py
```diff
diff --git a/fastapi_demo/app/api/v1/models.py b/fastapi_demo/app/api/v1/models.py
index 820e49c..7c444d5 100644
--- a/fastapi_demo/app/api/v1/models.py
+++ b/fastapi_demo/app/api/v1/models.py
@@ -1,6 +1,6 @@
 from typing import Any, Dict, List, Optional
 
-from pydantic import BaseModel, Field
+from pydantic import BaseModel, Field, model_validator
 
 
 class RecommendationRequest(BaseModel):
@@ -16,13 +16,39 @@ class RecommendationRequest(BaseModel):
         default=None,
         description="Client session id for resume; if empty server will generate one",
     )
+    conversation_id: Optional[str] = Field(
+        default=None,
+        description="Conversation id alias for session_id",
+    )
+
+    @model_validator(mode="after")
+    def normalize_ids(self):
+        if not self.session_id and self.conversation_id:
+            self.session_id = self.conversation_id
+        return self
 
 
 class RecommendationConfirmRequest(BaseModel):
-    session_id: str = Field(..., description="Session id returned by /recommend", min_length=1)
+    session_id: Optional[str] = Field(
+        default=None,
+        description="Session id returned by /recommend",
+        min_length=1,
+    )
     action: str = Field(default="confirm", pattern="^(confirm|edit)$")
     sub_questions: Optional[List[str]] = Field(default=None, description="Required when action=edit")
     comment: Optional[str] = Field(default=None, description="Human comment")
+    conversation_id: Optional[str] = Field(
+        default=None,
+        description="Conversation id alias for session_id",
+    )
+
+    @model_validator(mode="after")
+    def normalize_ids(self):
+        if not self.session_id and self.conversation_id:
+            self.session_id = self.conversation_id
+        if not self.session_id:
+            raise ValueError("session_id or conversation_id is required")
+        return self
 
 
 class RecommendationResponse(BaseModel):
@@ -46,5 +72,15 @@ class RecommendationResponse(BaseModel):
     )
 
 
+class SessionStateUpdateRequest(BaseModel):
+    facts: Dict[str, str] = Field(default_factory=dict, description="Structured session facts")
+
+
+class SessionStateResponse(BaseModel):
+    session_id: str = Field(..., description="Session id")
+    facts: Dict[str, str] = Field(default_factory=dict, description="Structured session facts")
+    updated_at: float = Field(..., description="Unix timestamp in seconds")
+
+
 class HealthCheckResponse(BaseModel):
     status: str = Field(..., description="Health status")
```

File: D:\Github\software_reco\fastapi_demo\app\api\v1\routes.py
```diff
diff --git a/fastapi_demo/app/api/v1/routes.py b/fastapi_demo/app/api/v1/routes.py
index 0519047..8e62415 100644
--- a/fastapi_demo/app/api/v1/routes.py
+++ b/fastapi_demo/app/api/v1/routes.py
@@ -1,4 +1,5 @@
 import logging
+import re
 import time
 from typing import Any, Dict, Optional
 from uuid import uuid4
@@ -10,6 +11,8 @@ from app.api.v1.models import (
     RecommendationConfirmRequest,
     RecommendationRequest,
     RecommendationResponse,
+    SessionStateResponse,
+    SessionStateUpdateRequest,
 )
 from software_recommend_system.rag_agent import create_rag_with_routing_agent
 from software_recommend_system.state import AgentState
@@ -19,6 +22,12 @@ router = APIRouter()
 logger = logging.getLogger(__name__)
 AGENT = create_rag_with_routing_agent()
 
+SESSION_STATE_STORE: Dict[str, Dict[str, Any]] = {}
+NAME_ZH_PATTERN = re.compile(
+    r"(?:(?:\u6211\u53eb|\u8bb0\u4f4f\u6211\u53eb|\u8bb0\u4f4f\u6211\u7684\u540d\u5b57\u662f|\u6211\u7684\u540d\u5b57\u662f)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}))"
+)
+NAME_EN_PATTERN = re.compile(r"(?i)(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z\-' ]{0,40})")
+
 
 def _get_value(result: Any, key: str, default: Any = None) -> Any:
     if isinstance(result, dict):
@@ -41,16 +50,65 @@ def _extract_interrupt_payload(result: Any) -> Optional[Dict[str, Any]]:
     return {"type": "human_confirmation", "message": str(payload)}
 
 
+def _extract_name_fact(query: str) -> Optional[str]:
+    if not query:
+        return None
+    zh = NAME_ZH_PATTERN.search(query)
+    if zh:
+        return zh.group(1).strip()
+    en = NAME_EN_PATTERN.search(query)
+    if en:
+        return en.group(1).strip()
+    return None
+
+
+def _is_asking_user_name(query: str) -> bool:
+    if not query:
+        return False
+    normalized = query.strip().lower()
+    return (
+        ("\u6211\u53eb\u4ec0\u4e48" in normalized)
+        or ("\u6211\u7684\u540d\u5b57" in normalized)
+        or ("what is my name" in normalized)
+        or ("who am i" in normalized)
+    )
+
+
+def _get_or_create_session_state(session_id: str) -> Dict[str, Any]:
+    existing = SESSION_STATE_STORE.get(session_id)
+    if existing:
+        return existing
+    created = {"facts": {}, "updated_at": time.time()}
+    SESSION_STATE_STORE[session_id] = created
+    return created
+
+
+def _merge_session_facts(session_id: str, facts: Dict[str, str]) -> Dict[str, Any]:
+    state = _get_or_create_session_state(session_id)
+    current_facts: Dict[str, str] = state.setdefault("facts", {})
+    for key, value in (facts or {}).items():
+        fact_key = (key or "").strip()
+        fact_value = (value or "").strip()
+        if fact_key and fact_value:
+            current_facts[fact_key] = fact_value
+    state["updated_at"] = time.time()
+    return state
+
+
+def _upsert_name_fact_from_query(session_id: str, query: str) -> Dict[str, Any]:
+    name = _extract_name_fact(query)
+    if not name:
+        return _get_or_create_session_state(session_id)
+    return _merge_session_facts(session_id, {"user_name": name})
+
+
 @router.post("/recommend", response_model=RecommendationResponse)
 async def get_software_recommendation(request_data: RecommendationRequest):
-    """
-    Get software recommendations.
-    - **query**: user query text
-    - **timeout**: timeout in seconds, default 60
-    - **max_iterations**: max iteration count, default 3
-    """
     try:
         session_id = request_data.session_id or uuid4().hex
+        session_state = _upsert_name_fact_from_query(session_id, request_data.query)
+        known_name = (session_state.get("facts") or {}).get("user_name")
+
         logger.info(
             "recommend request: session_id=%s timeout=%s max_iterations=%s query=%r",
             session_id,
@@ -59,8 +117,29 @@ async def get_software_recommendation(request_data: RecommendationRequest):
             request_data.query,
         )
 
+        if known_name and _is_asking_user_name(request_data.query):
+            return RecommendationResponse(
+                status="success",
+                final_answer=f"\u4f60\u53eb{known_name}\u3002",
+                candidates=[],
+                mode="chat",
+                iteration_count=0,
+                coverage=1.0,
+                session_id=session_id,
+                awaiting_human_confirmation=False,
+            )
+
+        effective_query = request_data.query
+        if known_name:
+            effective_query = (
+                f"{request_data.query}\n\n"
+                "[Known User Facts]\n"
+                f"user_name: {known_name}\n"
+                "If user asks identity-related questions, trust this fact."
+            )
+
         state = AgentState(
-            user_query=request_data.query,
+            user_query=effective_query,
             timeout_budget=request_data.timeout,
             max_iterations=request_data.max_iterations,
             start_time=time.time(),
@@ -141,6 +220,26 @@ async def confirm_software_recommendation(request_data: RecommendationConfirmReq
         ) from exc
 
 
+@router.get("/session-state/{session_id}", response_model=SessionStateResponse)
+async def get_session_state(session_id: str):
+    state = _get_or_create_session_state(session_id)
+    return SessionStateResponse(
+        session_id=session_id,
+        facts=state.get("facts", {}),
+        updated_at=float(state.get("updated_at", time.time())),
+    )
+
+
+@router.put("/session-state/{session_id}", response_model=SessionStateResponse)
+async def upsert_session_state(session_id: str, request_data: SessionStateUpdateRequest):
+    state = _merge_session_facts(session_id, request_data.facts or {})
+    return SessionStateResponse(
+        session_id=session_id,
+        facts=state.get("facts", {}),
+        updated_at=float(state.get("updated_at", time.time())),
+    )
+
+
 async def run_agent_async(agent, graph_input: Any, config: Optional[Dict[str, Any]] = None):
     """Run the agent asynchronously."""
     try:
@@ -189,51 +288,6 @@ async def initialize_database():
                     "tags": ["cache", "performance"],
                 },
             },
-            {
-                "content": (
-                    "Ehcache is an open-source, standards-based cache used to boost performance, "
-                    "offload your database and simplify scalability. Ehcache offers analysis "
-                    "and reporting, enabling you to monitor cache activity and performance."
-                ),
-                "metadata": {
-                    "source": "ehcache.org",
-                    "published_date": "2023-03-10",
-                    "author": "Terracotta Team",
-                    "url": "https://www.ehcache.org/",
-                    "source_ranking": 7.5,
-                    "tags": ["cache", "java", "spring"],
-                },
-            },
-            {
-                "content": (
-                    "Spring Boot is an open-source Java-based framework used to create stand-alone, "
-                    "production-grade Spring applications with minimum configurations. It simplifies "
-                    "the development process by providing default configurations."
-                ),
-                "metadata": {
-                    "source": "spring.io",
-                    "published_date": "2023-02-01",
-                    "author": "Pivotal Team",
-                    "url": "https://spring.io/projects/spring-boot",
-                    "source_ranking": 9.5,
-                    "tags": ["framework", "java", "spring"],
-                },
-            },
-            {
-                "content": (
-                    "Hibernate is an object-relational mapping tool for the Java programming language. "
-                    "It provides a framework for mapping an object-oriented domain model to a relational "
-                    "database and offers data query and retrieval facilities."
-                ),
-                "metadata": {
-                    "source": "hibernate.org",
-                    "published_date": "2023-01-20",
-                    "author": "Hibernate Team",
-                    "url": "https://hibernate.org/",
-                    "source_ranking": 8.5,
-                    "tags": ["orm", "java", "database"],
-                },
-            },
         ]
 
         success = initialize_vector_store(sample_docs)
@@ -244,4 +298,4 @@ async def initialize_database():
         raise HTTPException(
             status_code=500,
             detail=f"Error initializing database: {exc}",
-        ) from exc
+        ) from exc
\ No newline at end of file
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\client\FastApiClient.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java b/ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java
index 05d2541..f326b72 100644
--- a/ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java
@@ -21,42 +21,74 @@ public class FastApiClient {
     private static final Logger logger = LoggerFactory.getLogger(FastApiClient.class);
     private final RestTemplate restTemplate;
     private final String recommendUrl;
+    private final String sessionStateUrl;
 
     public FastApiClient(
             RestTemplate restTemplate,
             @Value("${app.fastapi.base-url}") String baseUrl,
-            @Value("${app.fastapi.recommend-path}") String recommendPath) {
+            @Value("${app.fastapi.recommend-path}") String recommendPath,
+            @Value("${app.fastapi.session-state-path:/api/v1/session-state}") String sessionStatePath) {
         this.restTemplate = restTemplate;
         this.recommendUrl = baseUrl + recommendPath;
+        this.sessionStateUrl = baseUrl + sessionStatePath;
     }
 
     public RecommendResponse recommend(RecommendRequest request) {
-        // 1. Header
         HttpHeaders headers = new HttpHeaders();
         headers.setContentType(MediaType.APPLICATION_JSON);
 
-        // 2. Body锛堜綘鐜板湪杩欎釜 payload 鏄?OK 鐨勶級
         Map<String, Object> payload = new HashMap<>();
         payload.put("query", request.getQuery());
         payload.put("timeout", request.getTimeout());
         payload.put("max_iterations", request.getMaxIterations());
+        if (request.getSessionId() != null && !request.getSessionId().isBlank()) {
+            payload.put("session_id", request.getSessionId());
+        }
 
-        // 3. HttpEntity = headers + body
         HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);
 
         logger.info(
                 "FastAPI request url={}, payload={}, entity={}",
                 recommendUrl, payload, entity);
 
-        // 4. exchange锛氬彂璇锋眰 + 鎷垮搷搴?         ResponseEntity<RecommendResponse> response = restTemplate.exchange(
                 recommendUrl,
                 HttpMethod.POST,
                 entity,
                 RecommendResponse.class);
 
-        // 5. 鐩存帴杩斿洖 body
         return response.getBody();
     }
 
+    public Map<String, Object> getSessionState(String sessionId) {
+        if (sessionId == null || sessionId.isBlank()) {
+            return Map.of();
+        }
+        ResponseEntity<Map> response = restTemplate.exchange(
+                sessionStateUrl + "/" + sessionId,
+                HttpMethod.GET,
+                new HttpEntity<>(new HttpHeaders()),
+                Map.class
+        );
+        return response.getBody() == null ? Map.of() : response.getBody();
+    }
+
+    public void upsertSessionState(String sessionId, Map<String, String> facts) {
+        if (sessionId == null || sessionId.isBlank()) {
+            return;
+        }
+        HttpHeaders headers = new HttpHeaders();
+        headers.setContentType(MediaType.APPLICATION_JSON);
+
+        Map<String, Object> payload = new HashMap<>();
+        payload.put("facts", facts == null ? Map.of() : facts);
+        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);
+
+        restTemplate.exchange(
+                sessionStateUrl + "/" + sessionId,
+                HttpMethod.PUT,
+                entity,
+                Map.class
+        );
+    }
 }
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\controller\ChatController.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java b/ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java
index d358a48..bad4aa3 100644
--- a/ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java
@@ -1,6 +1,9 @@
 package com.example.demo.controller;
 
 import com.example.demo.client.FastApiClient;
+import com.example.demo.conversation.entity.ConversationEntity;
+import com.example.demo.conversation.entity.ConversationMessageEntity;
+import com.example.demo.conversation.service.ConversationService;
 import com.example.demo.dto.Message;
 import com.example.demo.dto.OllamaChatRequest;
 import com.example.demo.dto.OllamaChatResponse;
@@ -12,41 +15,85 @@ import org.springframework.web.bind.annotation.PostMapping;
 import org.springframework.web.bind.annotation.RequestBody;
 import org.springframework.web.bind.annotation.RestController;
 
+import java.util.ArrayList;
+import java.util.Collections;
 import java.util.List;
+import java.util.Locale;
+import java.util.Map;
+import java.util.Optional;
+import java.util.regex.Matcher;
+import java.util.regex.Pattern;
 
 @RestController
 public class ChatController {
     private static final Logger logger = LoggerFactory.getLogger(ChatController.class);
+    private static final Pattern NAME_ZH_PATTERN = Pattern.compile(
+            "(?:(?:\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u53eb|\\u8bb0\\u4f4f\\u6211\\u7684\\u540d\\u5b57\\u662f|\\u6211\\u7684\\u540d\\u5b57\\u662f)\\s*([\\p{IsHan}A-Za-z][\\p{IsHan}A-Za-z0-9_\\-]{0,31}))"
+    );
+    private static final Pattern NAME_EN_PATTERN = Pattern.compile(
+            "(?i)(?:my name is|i am|i'm)\\s+([A-Za-z][A-Za-z\\-' ]{0,40})"
+    );
+
     private final FastApiClient fastApiClient;
+    private final ConversationService conversationService;
 
-    public ChatController(FastApiClient fastApiClient) {
+    public ChatController(FastApiClient fastApiClient, ConversationService conversationService) {
         this.fastApiClient = fastApiClient;
+        this.conversationService = conversationService;
     }
 
     @PostMapping("/api/chat")
     public OllamaChatResponse chat(@RequestBody OllamaChatRequest request) {
-        if (request.getMessages() == null) {
-            logger.info("Chat request messages is null. model={}, stream={}", request.getModel(), request.getStream());
-        } else {
-            for (int i = 0; i < request.getMessages().size(); i++) {
-                Message message = request.getMessages().get(i);
-                logger.info(
-                        "Chat message[{}]: role={}, content={}",
-                        i,
-                        message.getRole(),
-                        message.getContent()
-                );
-            }
-        }
+        logIncomingMessages(request);
 
         String query = extractLastUserMessage(request.getMessages());
         if (query == null || query.isBlank()) {
-            return buildResponse(request, "Please enter a message.");
+            return buildResponse(
+                    request,
+                    "Please enter a message.",
+                    request.getConversationId(),
+                    request.getConversationId()
+            );
+        }
+
+        ConversationEntity conversation = conversationService.ensureConversation(
+                request.getConversationId(),
+                query,
+                request.getModel()
+        );
+
+        ConversationMessageEntity userMessage = conversationService.appendMessage(conversation, "user", query);
+
+        extractUserName(query).ifPresent(name ->
+                conversationService.upsertFact(conversation.getId(), "user_name", name, userMessage.getId(), 0.99d)
+        );
+
+        Map<String, String> facts = conversationService.getFacts(conversation.getId());
+        String knownName = facts.get("user_name");
+
+        if (isAskingUserName(query) && knownName != null && !knownName.isBlank()) {
+            String memoryAnswer = "\u4f60\u53eb" + knownName + "\u3002";
+            conversationService.appendMessage(conversation, "assistant", memoryAnswer);
+            return buildResponse(request, memoryAnswer, conversation.getId(), conversation.getId());
+        }
+
+        String enrichedQuery = buildQueryWithContext(
+                query,
+                facts,
+                conversationService.getRecentMessages(conversation.getId(), 6)
+        );
+
+        try {
+            fastApiClient.upsertSessionState(conversation.getId(), facts);
+        } catch (Exception ex) {
+            logger.warn("FastAPI session-state sync failed: {}", ex.getMessage());
         }
 
         RecommendResponse fastapi = null;
         try {
-            fastapi = fastApiClient.recommend(new RecommendRequest(query, 60, 3));
+            fastapi = fastApiClient.recommend(
+                    new RecommendRequest(enrichedQuery, 60, 3, conversation.getId())
+            );
         } catch (Exception ex) {
             logger.warn("FastAPI recommend failed: {}", ex.getMessage());
         }
@@ -57,7 +104,32 @@ public class ChatController {
         } else if (fastapi == null && query != null && !query.isBlank()) {
             content = "FastAPI service unavailable. Please try again.";
         }
-        return buildResponse(request, content);
+
+        if (content != null && !content.isBlank()) {
+            conversationService.appendMessage(conversation, "assistant", content);
+        }
+
+        String sessionId = (fastapi != null && fastapi.getSessionId() != null && !fastapi.getSessionId().isBlank())
+                ? fastapi.getSessionId()
+                : conversation.getId();
+
+        return buildResponse(request, content, conversation.getId(), sessionId);
+    }
+
+    private void logIncomingMessages(OllamaChatRequest request) {
+        if (request.getMessages() == null) {
+            logger.info("Chat request messages is null. model={}, stream={}", request.getModel(), request.getStream());
+            return;
+        }
+        for (int i = 0; i < request.getMessages().size(); i++) {
+            Message message = request.getMessages().get(i);
+            logger.info(
+                    "Chat message[{}]: role={}, content={}",
+                    i,
+                    message.getRole(),
+                    message.getContent()
+            );
+        }
     }
 
     private String extractLastUserMessage(List<Message> messages) {
@@ -72,7 +144,92 @@ public class ChatController {
         return messages.get(messages.size() - 1).getContent();
     }
 
-    private OllamaChatResponse buildResponse(OllamaChatRequest request, String content) {
+    private Optional<String> extractUserName(String query) {
+        if (query == null || query.isBlank()) {
+            return Optional.empty();
+        }
+
+        Matcher zh = NAME_ZH_PATTERN.matcher(query);
+        if (zh.find()) {
+            return Optional.of(zh.group(1).trim());
+        }
+
+        Matcher en = NAME_EN_PATTERN.matcher(query);
+        if (en.find()) {
+            return Optional.of(en.group(1).trim());
+        }
+
+        return Optional.empty();
+    }
+
+    private boolean isAskingUserName(String query) {
+        if (query == null) {
+            return false;
+        }
+        String normalized = query.trim().toLowerCase(Locale.ROOT);
+        return normalized.contains("\u6211\u53eb\u4ec0\u4e48")
+                || normalized.contains("\u6211\u7684\u540d\u5b57")
+                || normalized.contains("what is my name")
+                || normalized.contains("who am i");
+    }
+
+    private String buildQueryWithContext(
+            String query,
+            Map<String, String> facts,
+            List<ConversationMessageEntity> recentMessages
+    ) {
+        if ((facts == null || facts.isEmpty()) && (recentMessages == null || recentMessages.isEmpty())) {
+            return capForRecommend(query);
+        }
+
+        StringBuilder sb = new StringBuilder();
+        sb.append("Current user input:\n").append(query.trim());
+
+        if (facts != null && !facts.isEmpty()) {
+            sb.append("\n\nKnown user facts:\n");
+            facts.forEach((k, v) -> sb.append("- ").append(k).append(": ").append(v).append("\n"));
+            sb.append("Use these facts when answering identity-related questions.\n");
+        }
+
+        if (recentMessages != null && !recentMessages.isEmpty()) {
+            List<ConversationMessageEntity> ordered = new ArrayList<>(recentMessages);
+            Collections.reverse(ordered);
+            sb.append("\nRecent conversation history:\n");
+            for (ConversationMessageEntity message : ordered) {
+                sb.append("- ")
+                        .append(message.getRole())
+                        .append(": ")
+                        .append(compact(message.getContent()))
+                        .append("\n");
+            }
+        }
+
+        String built = sb.toString();
+        return capForRecommend(built);
+    }
+
+    private String capForRecommend(String value) {
+        if (value == null) {
+            return "";
+        }
+        String normalized = value.trim();
+        return normalized.length() > 980 ? normalized.substring(0, 980) : normalized;
+    }
+
+    private String compact(String text) {
+        if (text == null) {
+            return "";
+        }
+        String normalized = text.replace('\n', ' ').replace('\r', ' ').trim();
+        return normalized.length() > 300 ? normalized.substring(0, 300) : normalized;
+    }
+
+    private OllamaChatResponse buildResponse(
+            OllamaChatRequest request,
+            String content,
+            String conversationId,
+            String sessionId
+    ) {
         Message message = new Message();
         message.setRole("assistant");
         message.setContent(content);
@@ -81,6 +238,8 @@ public class ChatController {
         response.setModel(request.getModel());
         response.setMessage(message);
         response.setDone(true);
+        response.setConversationId(conversationId);
+        response.setSessionId(sessionId);
         return response;
     }
 }
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\dto\OllamaChatResponse.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatResponse.java b/ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatResponse.java
index 7bd5885..74f134f 100644
--- a/ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatResponse.java
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatResponse.java
@@ -1,9 +1,15 @@
 package com.example.demo.dto;
 
+import com.fasterxml.jackson.annotation.JsonProperty;
+
 public class OllamaChatResponse {
     private String model;
     private Message message;
     private boolean done;
+    @JsonProperty("conversation_id")
+    private String conversationId;
+    @JsonProperty("session_id")
+    private String sessionId;
 
     public String getModel() { return model; }
     public void setModel(String model) { this.model = model; }
@@ -11,4 +17,8 @@ public class OllamaChatResponse {
     public void setMessage(Message message) { this.message = message; }
     public boolean isDone() { return done; }
     public void setDone(boolean done) { this.done = done; }
-}
\ No newline at end of file
+    public String getConversationId() { return conversationId; }
+    public void setConversationId(String conversationId) { this.conversationId = conversationId; }
+    public String getSessionId() { return sessionId; }
+    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\dto\RecommendResponse.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/dto/RecommendResponse.java b/ollama_springboot/demo/src/main/java/com/example/demo/dto/RecommendResponse.java
index f3d928d..07ea43e 100644
--- a/ollama_springboot/demo/src/main/java/com/example/demo/dto/RecommendResponse.java
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/dto/RecommendResponse.java
@@ -6,9 +6,13 @@ public class RecommendResponse {
     private String status;
     @JsonProperty("final_answer")
     private String finalAnswer;
+    @JsonProperty("session_id")
+    private String sessionId;
 
     public String getStatus() { return status; }
     public void setStatus(String status) { this.status = status; }
     public String getFinalAnswer() { return finalAnswer; }
     public void setFinalAnswer(String finalAnswer) { this.finalAnswer = finalAnswer; }
-}
\ No newline at end of file
+    public String getSessionId() { return sessionId; }
+    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\resources\application.yml
```diff
diff --git a/ollama_springboot/demo/src/main/resources/application.yml b/ollama_springboot/demo/src/main/resources/application.yml
index a1bd6db..a3ed2e1 100644
--- a/ollama_springboot/demo/src/main/resources/application.yml
+++ b/ollama_springboot/demo/src/main/resources/application.yml
@@ -4,10 +4,48 @@ server:
 spring:
   jackson:
     property-naming-strategy: SNAKE_CASE
+  datasource:
+    url: jdbc:h2:mem:software_reco;MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE
+    username: sa
+    password:
+    driver-class-name: org.h2.Driver
+  jpa:
+    hibernate:
+      ddl-auto: validate
+    open-in-view: false
+    properties:
+      hibernate:
+        dialect: org.hibernate.dialect.H2Dialect
+  flyway:
+    enabled: true
+    locations: classpath:db/migration
+  h2:
+    console:
+      enabled: true
+      path: /h2-console
 
 app:
   fastapi:
     base-url: http://127.0.0.1:8000
     recommend-path: /api/v1/recommend
+    session-state-path: /api/v1/session-state
   cors:
     allowed-origins: http://localhost:3000,http://127.0.0.1:3000
+
+---
+spring:
+  config:
+    activate:
+      on-profile: postgres
+  datasource:
+    url: ${DB_URL:jdbc:postgresql://localhost:5432/software_reco}
+    username: ${DB_USER:postgres}
+    password: ${DB_PASSWORD:postgres}
+    driver-class-name: org.postgresql.Driver
+  jpa:
+    properties:
+      hibernate:
+        dialect: org.hibernate.dialect.PostgreSQLDialect
+  h2:
+    console:
+      enabled: false
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\resources\db\migration\V2__create_conversation_facts.sql
```diff
diff --git a/ollama_springboot/demo/src/main/resources/db/migration/V2__create_conversation_facts.sql b/ollama_springboot/demo/src/main/resources/db/migration/V2__create_conversation_facts.sql
new file mode 100644
index 0000000..483587d
--- /dev/null
+++ b/ollama_springboot/demo/src/main/resources/db/migration/V2__create_conversation_facts.sql
@@ -0,0 +1,16 @@
+CREATE TABLE conversation_facts (
+    id VARCHAR(36) PRIMARY KEY,
+    conversation_id VARCHAR(36) NOT NULL,
+    fact_key VARCHAR(64) NOT NULL,
+    fact_value VARCHAR(500) NOT NULL,
+    source_message_id VARCHAR(36),
+    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
+    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
+    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
+    CONSTRAINT fk_conversation_facts_conversation
+      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
+    CONSTRAINT uk_conversation_facts_key UNIQUE (conversation_id, fact_key)
+);
+
+CREATE INDEX idx_conversation_facts_updated_at
+  ON conversation_facts(conversation_id, updated_at DESC);
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\entity\ConversationFactEntity.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/entity/ConversationFactEntity.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/entity/ConversationFactEntity.java
new file mode 100644
index 0000000..2b8c640
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/entity/ConversationFactEntity.java
@@ -0,0 +1,74 @@
+package com.example.demo.conversation.entity;
+
+import jakarta.persistence.Column;
+import jakarta.persistence.Entity;
+import jakarta.persistence.FetchType;
+import jakarta.persistence.Id;
+import jakarta.persistence.JoinColumn;
+import jakarta.persistence.ManyToOne;
+import jakarta.persistence.PrePersist;
+import jakarta.persistence.PreUpdate;
+import jakarta.persistence.Table;
+import jakarta.persistence.UniqueConstraint;
+import lombok.Getter;
+import lombok.Setter;
+
+import java.time.Instant;
+import java.util.UUID;
+
+@Getter
+@Setter
+@Entity
+@Table(
+        name = "conversation_facts",
+        uniqueConstraints = @UniqueConstraint(
+                name = "uk_conversation_facts_key",
+                columnNames = {"conversation_id", "fact_key"}
+        )
+)
+public class ConversationFactEntity {
+    @Id
+    @Column(length = 36, nullable = false, updatable = false)
+    private String id;
+
+    @ManyToOne(fetch = FetchType.LAZY, optional = false)
+    @JoinColumn(name = "conversation_id", nullable = false)
+    private ConversationEntity conversation;
+
+    @Column(name = "fact_key", nullable = false, length = 64)
+    private String factKey;
+
+    @Column(name = "fact_value", nullable = false, length = 500)
+    private String factValue;
+
+    @Column(name = "source_message_id", length = 36)
+    private String sourceMessageId;
+
+    @Column(nullable = false)
+    private double confidence = 1.0d;
+
+    @Column(name = "created_at", nullable = false, updatable = false)
+    private Instant createdAt;
+
+    @Column(name = "updated_at", nullable = false)
+    private Instant updatedAt;
+
+    @PrePersist
+    public void prePersist() {
+        Instant now = Instant.now();
+        if (id == null || id.isBlank()) {
+            id = UUID.randomUUID().toString();
+        }
+        if (createdAt == null) {
+            createdAt = now;
+        }
+        if (updatedAt == null) {
+            updatedAt = now;
+        }
+    }
+
+    @PreUpdate
+    public void preUpdate() {
+        updatedAt = Instant.now();
+    }
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\repository\ConversationFactRepository.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationFactRepository.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationFactRepository.java
new file mode 100644
index 0000000..0fe53bc
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationFactRepository.java
@@ -0,0 +1,13 @@
+package com.example.demo.conversation.repository;
+
+import com.example.demo.conversation.entity.ConversationFactEntity;
+import org.springframework.data.jpa.repository.JpaRepository;
+
+import java.util.List;
+import java.util.Optional;
+
+public interface ConversationFactRepository extends JpaRepository<ConversationFactEntity, String> {
+    Optional<ConversationFactEntity> findByConversation_IdAndFactKey(String conversationId, String factKey);
+
+    List<ConversationFactEntity> findByConversation_IdOrderByUpdatedAtDesc(String conversationId);
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\repository\ConversationMessageRepository.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationMessageRepository.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationMessageRepository.java
new file mode 100644
index 0000000..3806ad4
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/repository/ConversationMessageRepository.java
@@ -0,0 +1,10 @@
+package com.example.demo.conversation.repository;
+
+import com.example.demo.conversation.entity.ConversationMessageEntity;
+import org.springframework.data.domain.Page;
+import org.springframework.data.domain.Pageable;
+import org.springframework.data.jpa.repository.JpaRepository;
+
+public interface ConversationMessageRepository extends JpaRepository<ConversationMessageEntity, String> {
+    Page<ConversationMessageEntity> findByConversation_IdOrderByCreatedAtDesc(String conversationId, Pageable pageable);
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\service\ConversationService.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/service/ConversationService.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/service/ConversationService.java
new file mode 100644
index 0000000..9da491f
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/service/ConversationService.java
@@ -0,0 +1,325 @@
+package com.example.demo.conversation.service;
+
+import com.example.demo.conversation.dto.ConversationApiModels;
+import com.example.demo.conversation.entity.ConversationEntity;
+import com.example.demo.conversation.entity.ConversationFactEntity;
+import com.example.demo.conversation.entity.ConversationMessageEntity;
+import com.example.demo.conversation.repository.ConversationFactRepository;
+import com.example.demo.conversation.repository.ConversationMessageRepository;
+import com.example.demo.conversation.repository.ConversationRepository;
+import jakarta.persistence.criteria.Predicate;
+import org.springframework.data.domain.Page;
+import org.springframework.data.domain.PageRequest;
+import org.springframework.data.domain.Pageable;
+import org.springframework.data.domain.Sort;
+import org.springframework.data.jpa.domain.Specification;
+import org.springframework.http.HttpStatus;
+import org.springframework.stereotype.Service;
+import org.springframework.transaction.annotation.Transactional;
+import org.springframework.web.server.ResponseStatusException;
+
+import java.time.Instant;
+import java.util.ArrayList;
+import java.util.LinkedHashMap;
+import java.util.List;
+import java.util.Locale;
+import java.util.Map;
+
+@Service
+public class ConversationService {
+    private final ConversationRepository conversationRepository;
+    private final ConversationMessageRepository messageRepository;
+    private final ConversationFactRepository factRepository;
+
+    public ConversationService(
+            ConversationRepository conversationRepository,
+            ConversationMessageRepository messageRepository,
+            ConversationFactRepository factRepository
+    ) {
+        this.conversationRepository = conversationRepository;
+        this.messageRepository = messageRepository;
+        this.factRepository = factRepository;
+    }
+
+    @Transactional(readOnly = true)
+    public ConversationApiModels.ListResponse list(
+            int page,
+            int pageSize,
+            String q,
+            boolean includeArchived,
+            Sort sort
+    ) {
+        Pageable pageable = PageRequest.of(Math.max(page - 1, 0), Math.max(1, Math.min(pageSize, 100)), sort);
+        Specification<ConversationEntity> spec = (root, query, cb) -> {
+            List<Predicate> predicates = new ArrayList<>();
+            predicates.add(cb.isFalse(root.get("deleted")));
+            if (!includeArchived) {
+                predicates.add(cb.isFalse(root.get("archived")));
+            }
+            if (q != null && !q.isBlank()) {
+                String like = "%" + q.trim().toLowerCase(Locale.ROOT) + "%";
+                predicates.add(
+                        cb.or(
+                                cb.like(cb.lower(root.get("title")), like),
+                                cb.like(cb.lower(root.get("preview")), like)
+                        )
+                );
+            }
+            return cb.and(predicates.toArray(new Predicate[0]));
+        };
+
+        Page<ConversationEntity> pageResult = conversationRepository.findAll(spec, pageable);
+        List<ConversationApiModels.ListItem> items = pageResult.getContent().stream()
+                .map(c -> new ConversationApiModels.ListItem(
+                        c.getId(),
+                        c.getTitle(),
+                        c.getPreview(),
+                        c.getMessageCount(),
+                        c.getLastMessageRole(),
+                        c.getLastMessageAt(),
+                        c.getUpdatedAt(),
+                        c.getCreatedAt(),
+                        c.isArchived()
+                ))
+                .toList();
+
+        return new ConversationApiModels.ListResponse(
+                items,
+                new ConversationApiModels.Pagination(
+                        pageResult.getNumber() + 1,
+                        pageResult.getSize(),
+                        pageResult.getTotalElements(),
+                        pageResult.getTotalPages(),
+                        pageResult.hasNext(),
+                        pageResult.hasPrevious()
+                )
+        );
+    }
+
+    @Transactional
+    public ConversationApiModels.CreateResponse create(ConversationApiModels.CreateRequest request) {
+        String role = normalizeRole(request.firstMessage().role());
+        if (!"user".equals(role)) {
+            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "first_message.role must be user");
+        }
+        String content = request.firstMessage().content().trim();
+
+        ConversationEntity conversation = new ConversationEntity();
+        conversation.setTitle(resolveTitle(request.title(), content));
+        conversation.setPreview(content.length() > 500 ? content.substring(0, 500) : content);
+        conversation.setModelName(trimToNull(request.model()));
+        conversation = conversationRepository.save(conversation);
+
+        ConversationMessageEntity message = new ConversationMessageEntity();
+        message.setConversation(conversation);
+        message.setRole(role);
+        message.setContent(content);
+        message = messageRepository.save(message);
+
+        conversation.setMessageCount(1);
+        conversation.setLastMessageRole(message.getRole());
+        conversation.setLastMessageAt(message.getCreatedAt());
+        conversation.setUpdatedAt(message.getCreatedAt());
+        conversation = conversationRepository.save(conversation);
+
+        return new ConversationApiModels.CreateResponse(
+                conversation.getId(),
+                conversation.getTitle(),
+                conversation.getCreatedAt(),
+                conversation.getUpdatedAt(),
+                conversation.getMessageCount(),
+                List.of(
+                        new ConversationApiModels.MessageItem(
+                                message.getId(),
+                                message.getRole(),
+                                message.getContent(),
+                                message.getCreatedAt()
+                        )
+                )
+        );
+    }
+
+    @Transactional
+    public ConversationApiModels.UpdateResponse patch(String id, ConversationApiModels.UpdateRequest request) {
+        if (!request.hasAnyField()) {
+            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least one field is required");
+        }
+
+        ConversationEntity conversation = findActiveConversation(id);
+
+        if (request.title() != null) {
+            conversation.setTitle(resolveTitle(request.title(), conversation.getPreview()));
+        }
+        if (request.archived() != null) {
+            conversation.setArchived(request.archived());
+        }
+        if (request.pinned() != null) {
+            conversation.setPinned(request.pinned());
+        }
+
+        conversation = conversationRepository.save(conversation);
+        return new ConversationApiModels.UpdateResponse(
+                conversation.getId(),
+                conversation.getTitle(),
+                conversation.isArchived(),
+                conversation.isPinned(),
+                conversation.getUpdatedAt()
+        );
+    }
+
+    @Transactional
+    public ConversationApiModels.DeleteResponse delete(String id, boolean hard) {
+        ConversationEntity conversation = findActiveConversation(id);
+
+        Instant deletedAt = Instant.now();
+        if (hard) {
+            conversationRepository.delete(conversation);
+        } else {
+            conversation.setDeleted(true);
+            conversation.setArchived(true);
+            conversation.setUpdatedAt(deletedAt);
+            conversationRepository.save(conversation);
+        }
+        return new ConversationApiModels.DeleteResponse(id, true, hard, deletedAt);
+    }
+
+    @Transactional
+    public ConversationEntity ensureConversation(String id, String titleSeed, String modelName) {
+        String normalizedId = trimToNull(id);
+        if (normalizedId != null) {
+            return conversationRepository.findByIdAndDeletedFalse(normalizedId)
+                    .orElseGet(() -> {
+                        ConversationEntity created = new ConversationEntity();
+                        created.setId(normalizedId);
+                        created.setTitle(resolveTitle(titleSeed, titleSeed));
+                        created.setModelName(trimToNull(modelName));
+                        return conversationRepository.save(created);
+                    });
+        }
+
+        ConversationEntity created = new ConversationEntity();
+        created.setTitle(resolveTitle(titleSeed, titleSeed));
+        created.setModelName(trimToNull(modelName));
+        return conversationRepository.save(created);
+    }
+
+    @Transactional(readOnly = true)
+    public ConversationEntity findActiveConversation(String id) {
+        return conversationRepository.findByIdAndDeletedFalse(id)
+                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Conversation not found"));
+    }
+
+    @Transactional
+    public ConversationMessageEntity appendMessage(String conversationId, String role, String content) {
+        return appendMessage(findActiveConversation(conversationId), role, content);
+    }
+
+    @Transactional
+    public ConversationMessageEntity appendMessage(ConversationEntity conversation, String role, String content) {
+        String normalizedContent = trimToNull(content);
+        if (normalizedContent == null) {
+            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "message content is required");
+        }
+
+        ConversationMessageEntity message = new ConversationMessageEntity();
+        message.setConversation(conversation);
+        message.setRole(normalizeRole(role));
+        message.setContent(normalizedContent);
+        message = messageRepository.save(message);
+
+        conversation.setMessageCount(conversation.getMessageCount() + 1);
+        conversation.setLastMessageRole(message.getRole());
+        conversation.setLastMessageAt(message.getCreatedAt());
+        if ("user".equals(message.getRole())) {
+            conversation.setPreview(normalizedContent.length() > 500 ? normalizedContent.substring(0, 500) : normalizedContent);
+        }
+        conversation.setUpdatedAt(message.getCreatedAt());
+        conversationRepository.save(conversation);
+
+        return message;
+    }
+
+    @Transactional(readOnly = true)
+    public List<ConversationMessageEntity> getRecentMessages(String conversationId, int limit) {
+        int size = Math.max(1, Math.min(limit, 50));
+        return messageRepository.findByConversation_IdOrderByCreatedAtDesc(conversationId, PageRequest.of(0, size)).getContent();
+    }
+
+    @Transactional(readOnly = true)
+    public Map<String, String> getFacts(String conversationId) {
+        List<ConversationFactEntity> facts = factRepository.findByConversation_IdOrderByUpdatedAtDesc(conversationId);
+        Map<String, String> out = new LinkedHashMap<>();
+        for (ConversationFactEntity fact : facts) {
+            out.putIfAbsent(fact.getFactKey(), fact.getFactValue());
+        }
+        return out;
+    }
+
+    @Transactional
+    public Map<String, String> upsertFacts(String conversationId, Map<String, String> updates, String sourceMessageId, double confidence) {
+        if (updates == null || updates.isEmpty()) {
+            return getFacts(conversationId);
+        }
+        for (Map.Entry<String, String> entry : updates.entrySet()) {
+            upsertFact(conversationId, entry.getKey(), entry.getValue(), sourceMessageId, confidence);
+        }
+        return getFacts(conversationId);
+    }
+
+    @Transactional
+    public void upsertFact(String conversationId, String key, String value, String sourceMessageId, double confidence) {
+        String factKey = trimToNull(key);
+        String factValue = trimToNull(value);
+        if (factKey == null || factValue == null) {
+            return;
+        }
+
+        ConversationEntity conversation = findActiveConversation(conversationId);
+        ConversationFactEntity fact = factRepository.findByConversation_IdAndFactKey(conversationId, factKey)
+                .orElseGet(() -> {
+                    ConversationFactEntity created = new ConversationFactEntity();
+                    created.setConversation(conversation);
+                    created.setFactKey(factKey);
+                    return created;
+                });
+
+        fact.setFactValue(factValue);
+        fact.setSourceMessageId(trimToNull(sourceMessageId));
+        fact.setConfidence(Math.max(0.0d, Math.min(confidence, 1.0d)));
+        factRepository.save(fact);
+
+        conversation.setUpdatedAt(Instant.now());
+        conversationRepository.save(conversation);
+    }
+
+    private String normalizeRole(String role) {
+        String value = trimToNull(role);
+        if (value == null) {
+            return "user";
+        }
+        value = value.toLowerCase(Locale.ROOT);
+        if (!"user".equals(value) && !"assistant".equals(value) && !"system".equals(value)) {
+            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid role");
+        }
+        return value;
+    }
+
+    private String resolveTitle(String title, String fallback) {
+        String resolved = trimToNull(title);
+        if (resolved == null) {
+            resolved = trimToNull(fallback);
+        }
+        if (resolved == null) {
+            resolved = "New Conversation";
+        }
+        return resolved.length() > 200 ? resolved.substring(0, 200) : resolved;
+    }
+
+    private String trimToNull(String value) {
+        if (value == null) {
+            return null;
+        }
+        String trimmed = value.trim();
+        return trimmed.isEmpty() ? null : trimmed;
+    }
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\dto\SessionStateApiModels.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/dto/SessionStateApiModels.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/dto/SessionStateApiModels.java
new file mode 100644
index 0000000..2b5294f
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/dto/SessionStateApiModels.java
@@ -0,0 +1,25 @@
+package com.example.demo.conversation.dto;
+
+import jakarta.validation.constraints.Size;
+
+import java.time.Instant;
+import java.util.Map;
+
+public final class SessionStateApiModels {
+    private SessionStateApiModels() {
+    }
+
+    public record UpsertRequest(
+            @Size(max = 100) String userName,
+            Map<String, String> facts
+    ) {
+    }
+
+    public record Response(
+            String conversationId,
+            String sessionId,
+            Map<String, String> facts,
+            Instant updatedAt
+    ) {
+    }
+}
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\controller\SessionStateController.java
```diff
diff --git a/ollama_springboot/demo/src/main/java/com/example/demo/conversation/controller/SessionStateController.java b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/controller/SessionStateController.java
new file mode 100644
index 0000000..82bfab9
--- /dev/null
+++ b/ollama_springboot/demo/src/main/java/com/example/demo/conversation/controller/SessionStateController.java
@@ -0,0 +1,60 @@
+package com.example.demo.conversation.controller;
+
+import com.example.demo.conversation.dto.SessionStateApiModels;
+import com.example.demo.conversation.entity.ConversationEntity;
+import com.example.demo.conversation.service.ConversationService;
+import jakarta.validation.Valid;
+import org.springframework.web.bind.annotation.GetMapping;
+import org.springframework.web.bind.annotation.PathVariable;
+import org.springframework.web.bind.annotation.PutMapping;
+import org.springframework.web.bind.annotation.RequestBody;
+import org.springframework.web.bind.annotation.RequestMapping;
+import org.springframework.web.bind.annotation.RestController;
+
+import java.util.LinkedHashMap;
+import java.util.Map;
+
+@RestController
+@RequestMapping("/api/session-state")
+public class SessionStateController {
+    private final ConversationService conversationService;
+
+    public SessionStateController(ConversationService conversationService) {
+        this.conversationService = conversationService;
+    }
+
+    @GetMapping("/{conversationId}")
+    public SessionStateApiModels.Response get(@PathVariable String conversationId) {
+        ConversationEntity conversation = conversationService.findActiveConversation(conversationId);
+        return new SessionStateApiModels.Response(
+                conversation.getId(),
+                conversation.getId(),
+                conversationService.getFacts(conversationId),
+                conversation.getUpdatedAt()
+        );
+    }
+
+    @PutMapping("/{conversationId}")
+    public SessionStateApiModels.Response upsert(
+            @PathVariable String conversationId,
+            @Valid @RequestBody SessionStateApiModels.UpsertRequest request
+    ) {
+        ConversationEntity conversation = conversationService.ensureConversation(conversationId, "New Conversation", null);
+        Map<String, String> updates = new LinkedHashMap<>();
+        if (request.facts() != null) {
+            updates.putAll(request.facts());
+        }
+        if (request.userName() != null && !request.userName().isBlank()) {
+            updates.put("user_name", request.userName().trim());
+        }
+
+        Map<String, String> merged = conversationService.upsertFacts(conversation.getId(), updates, null, 1.0d);
+        ConversationEntity refreshed = conversationService.findActiveConversation(conversation.getId());
+        return new SessionStateApiModels.Response(
+                refreshed.getId(),
+                refreshed.getId(),
+                merged,
+                refreshed.getUpdatedAt()
+        );
+    }
+}
```

File: D:\Github\software_reco\steps\session_state_api_contract.md
```diff
diff --git a/steps/session_state_api_contract.md b/steps/session_state_api_contract.md
new file mode 100644
index 0000000..c7417d2
--- /dev/null
+++ b/steps/session_state_api_contract.md
@@ -0,0 +1,65 @@
+# Session State API Contract
+
+## Spring Boot
+
+### GET `/api/session-state/{conversation_id}`
+- Purpose: Read persisted session facts for a conversation.
+- Path params:
+  - `conversation_id` (string, required)
+- Response:
+```json
+{
+  "conversation_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
+  "session_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
+  "facts": {
+    "user_name": "Qiu Yuchen"
+  },
+  "updated_at": "2026-02-15T22:01:33Z"
+}
+```
+
+### PUT `/api/session-state/{conversation_id}`
+- Purpose: Upsert persisted session facts for a conversation.
+- Path params:
+  - `conversation_id` (string, required)
+- Request body:
+```json
+{
+  "user_name": "Qiu Yuchen",
+  "facts": {
+    "user_name": "Qiu Yuchen"
+  }
+}
+```
+- Response: same schema as GET.
+
+## FastAPI
+
+### GET `/api/v1/session-state/{session_id}`
+- Purpose: Read in-memory session facts used by agent runtime.
+- Path params:
+  - `session_id` (string, required)
+- Response:
+```json
+{
+  "session_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
+  "facts": {
+    "user_name": "Qiu Yuchen"
+  },
+  "updated_at": 1768500000.123
+}
+```
+
+### PUT `/api/v1/session-state/{session_id}`
+- Purpose: Upsert in-memory session facts used by agent runtime.
+- Path params:
+  - `session_id` (string, required)
+- Request body:
+```json
+{
+  "facts": {
+    "user_name": "Qiu Yuchen"
+  }
+}
+```
+- Response: same schema as GET.
```




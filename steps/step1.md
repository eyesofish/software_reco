##### Needs Update List:

1. `ollama_springboot/demo/src/main/java/com/example/demo/controller/ChatController.java`
   - responsibility: 接收 `POST /api/chat`，提取最后一条用户消息并转发到 FastAPI，返回 assistant 文本。
   - missing for conversation list: 未生成或管理会话 ID；未保存消息历史；无会话列表/详情/删除等接口。
   - suggestion scope (model / API / service / db): API, service

2. `ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatRequest.java` + `ollama_springboot/demo/src/main/java/com/example/demo/dto/OllamaChatResponse.java` + `ollama_springboot/demo/src/main/java/com/example/demo/dto/Message.java`
   - responsibility: 定义聊天入参与出参结构（model/messages/stream 与返回 message）。
   - missing for conversation list: DTO 不包含 `conversation_id`、`title`、`created_at`、`updated_at`、分页游标等会话管理字段。
   - suggestion scope (model / API): model, API

3. `ollama_springboot/demo/src/main/resources/application.yml`
   - responsibility: 配置 Spring 服务端口、FastAPI 转发地址与 CORS。
   - missing for conversation list: 无任何会话持久化配置（datasource、JPA、连接池、迁移配置）；说明当前后端无会话存储表可复用。
   - suggestion scope (db / service): db, service

4. `fastapi_demo/app/api/v1/routes.py`
   - responsibility: 提供 `/recommend`、`/recommend/confirm`、`/initialize-db`，通过 `session_id` 驱动 Agent 续跑。
   - missing for conversation list: 仅支持“执行链恢复”语义，不提供会话创建/查询列表/查询详情/归档删除；`session_id` 生命周期未持久化管理。
   - suggestion scope (API / service): API, service

5. `fastapi_demo/app/api/v1/models.py`
   - responsibility: 定义推荐请求/确认请求/响应模型（含 `session_id` 与 human confirmation 字段）。
   - missing for conversation list: 无会话实体模型（conversation/message）；无会话列表查询模型与分页过滤模型。
   - suggestion scope (model / API): model, API

6. `fastapi_demo/software_recommend_system/rag_agent.py`
   - responsibility: 构建 LangGraph 工作流并使用 `MemorySaver` checkpointer 进行线程状态保存。
   - missing for conversation list: `MemorySaver` 为进程内内存状态，不是可查询的会话存储；重启后不可恢复，不支持会话列表检索。
   - suggestion scope (service / db): service, db

7. `fastapi_demo/software_recommend_system/state.py`
   - responsibility: 定义 `AgentState`（含 `messages`、`session_id`、`pending_sub_questions` 等运行态字段）。
   - missing for conversation list: 当前是运行时状态对象，不是持久化数据模型；缺少可落库的 conversation/message schema。
   - suggestion scope (model / db): model, db

8. `ollama_springboot/demo/src/main/java/com/example/demo`（整体包结构）
   - responsibility: 当前仅含 controller/client/dto/config，负责请求转发与配置。
   - missing for conversation list: 缺失典型会话管理层（Entity/Repository/Service）；未发现可复用会话表或 ORM 映射。
   - suggestion scope (model / service / db): model, service, db

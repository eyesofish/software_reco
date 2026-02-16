# fastapi_demo 傻瓜教程（新接手必读）

这份文档只讲 `D:\Github\software_reco\fastapi_demo`。

目标：
- 你能把服务跑起来。
- 你知道每个目录/文件是干什么的。
- 你能快速定位“我要改哪段代码”。

---

## 1. 先跑起来（最短路径）

### 1.1 进入目录
```powershell
cd D:\Github\software_reco\fastapi_demo
```

### 1.2 安装依赖
```powershell
pip install -r requirements.txt
```

### 1.3 启动服务（两种方式）
```powershell
python main.py
```
或
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 1.4 健康检查
浏览器打开：
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

---

## 2. 项目是两层结构

`fastapi_demo` 里核心是两层：

1. `app/`：FastAPI 封装层（接口、参数校验、会话状态接口）。
2. `software_recommend_system/`：RAG/Agent 业务层（节点、状态、检索、图编排）。

可以理解为：
- `app` 负责“收请求、发响应、维护会话入口”。
- `software_recommend_system` 负责“真正推理和推荐”。

---

## 3. 请求主线（你最该先懂这个）

以 `POST /api/v1/recommend` 为例：

1. 请求进入 FastAPI 路由  
文件：`fastapi_demo/app/api/v1/routes.py`  
函数：`get_software_recommendation`

2. 读取/更新会话状态（facts + messages）  
文件：`fastapi_demo/app/api/v1/routes.py`  
函数：`_get_or_create_session_state`、`_merge_session_facts`、`_append_session_messages`

3. 构造 `AgentState` 并调用 LangGraph  
文件：`fastapi_demo/app/api/v1/routes.py`  
函数：`run_agent_async`  
文件：`fastapi_demo/software_recommend_system/rag_agent.py`  
函数：`create_rag_with_routing_agent`

4. 图里执行节点（路由到 rag/chat/draw）  
文件：`fastapi_demo/software_recommend_system/nodes.py`  
关键函数：`entry_node`、`routing_node`、`query_normalization_node` 等

5. 返回 `RecommendationResponse`  
文件：`fastapi_demo/app/api/v1/models.py`  
模型：`RecommendationResponse`

---

## 4. 文件定位总表（去哪改什么）

| 文件 | 作用 | 你会在什么场景改它 |
|---|---|---|
| `fastapi_demo/main.py` | 启动入口（`uvicorn.run`） | 改启动参数、端口、reload |
| `fastapi_demo/requirements.txt` | 顶层依赖入口 | 新增 FastAPI 相关依赖 |
| `fastapi_demo/app/main.py` | FastAPI app 装配（CORS、路由、异常处理） | 改全局中间件、异常日志 |
| `fastapi_demo/app/core/config.py` | FastAPI 环境变量配置 | 加/改服务配置项 |
| `fastapi_demo/app/core/startup.py` | 启动钩子 | 启动时预加载逻辑 |
| `fastapi_demo/app/api/v1/models.py` | 请求/响应 Pydantic 模型 | 加字段、改参数校验 |
| `fastapi_demo/app/api/v1/routes.py` | 所有 API 路由 + 会话状态维护 | 改接口行为、会话记忆策略 |
| `fastapi_demo/app/api/v1/startup_ingest.py` | 启动自动文档入库 | 改启动扫描目录、文件解析、自动入库条件 |
| `fastapi_demo/app/services/recommendation_service.py` | 旧服务层封装（当前主要走 routes 直调） | 想做服务层抽象时 |
| `fastapi_demo/software_recommend_system/state.py` | Agent 全量状态结构 `AgentState` | 新增状态字段、修状态默认值 |
| `fastapi_demo/software_recommend_system/rag_agent.py` | LangGraph 图编排 + checkpointer | 改流程拓扑、换持久化 checkpoint |
| `fastapi_demo/software_recommend_system/nodes.py` | 具体节点实现（RAG/Chat/Draw） | 改检索逻辑、改回答生成 |
| `fastapi_demo/software_recommend_system/tools.py` | 检索/绘图工具（向量检索 + Tavily） | 改搜索来源、改 tool 逻辑 |
| `fastapi_demo/software_recommend_system/utils.py` | 向量入库总入口（编排 loader/chunker/embedder/indexer） | 改 ingest 编排流程 |
| `fastapi_demo/software_recommend_system/ingestion/loader.py` | 文档标准化模块 | 改原始文档清洗逻辑 |
| `fastapi_demo/software_recommend_system/ingestion/chunker.py` | 文本切块模块 | 改 chunk 大小、重叠和 metadata |
| `fastapi_demo/software_recommend_system/ingestion/embedder.py` | 向量化模块（唯一 embeddings 调用） | 改 embedding 模型调用 |
| `fastapi_demo/software_recommend_system/ingestion/indexer.py` | Chroma 持久化写入模块 | 改集合写入/upsert 策略 |
| `fastapi_demo/software_recommend_system/config.py` | 业务层配置（模型、向量库参数） | 改 LLM/embedding/tool 配置 |
| `fastapi_demo/software_recommend_system/document_schema.py` | 文档数据结构（Document/Metadata） | 扩展文档元信息字段 |
| `fastapi_demo/software_recommend_system/run_agent.py` | CLI 调试入口 | 本地命令行调试节点流程 |
| `fastapi_demo/software_recommend_system/Workflow.txt` | 业务流程说明文档 | 对照理解整体流程 |
| `fastapi_demo/software_recommend_system/Rules.txt` | 规则说明文档 | 对齐接口契约和规则 |

---

## 5. 关键接口说明（函数级别，精确定位）

说明：
- 下方行号基于当前仓库版本，后续改代码后可能变化。
- 先看路由函数，再看它调用的 helper 函数，这样最快定位问题。

### 5.1 推荐主接口：`POST /api/v1/recommend`

接口入口：
- `fastapi_demo/app/api/v1/routes.py:270`
- 函数：`get_software_recommendation(request_data: RecommendationRequest)`

入参模型：
- `fastapi_demo/app/api/v1/models.py:6`
- 类：`RecommendationRequest`
- 字段：`query`、`timeout`、`max_iterations`、`session_id`、`conversation_id`
- ID 归一化逻辑：`normalize_ids`（`conversation_id -> session_id`）

出参模型：
- `fastapi_demo/app/api/v1/models.py:54`
- 类：`RecommendationResponse`

该接口内部调用链（按执行顺序）：
1. 会话 ID 生成或复用  
`session_id = request_data.session_id or uuid4().hex`
2. 解析并更新用户事实（比如 user_name）  
函数：`_upsert_name_fact_from_query`  
位置：`fastapi_demo/app/api/v1/routes.py:262`
3. 读取会话 facts/messages  
函数：`_normalize_session_messages`  
位置：`fastapi_demo/app/api/v1/routes.py:70`
4. 若命中“问我是谁”直接短路回答  
函数：`_is_asking_user_name`  
位置：`fastapi_demo/app/api/v1/routes.py:198`
5. 构建 `AgentState` 并调用图  
调用：`run_agent_async(AGENT, state, config)`  
位置：`fastapi_demo/app/api/v1/routes.py:437`
6. 处理 LangGraph interrupt（HITL）  
函数：`_extract_interrupt_payload`  
位置：`fastapi_demo/app/api/v1/routes.py:144`
7. 将 user/assistant 消息追加回会话存储  
函数：`_append_session_messages`  
位置：`fastapi_demo/app/api/v1/routes.py:247`

调试时重点看：
- `session_id` 是否持续一致
- `state.messages` 是否正确注入
- `RecommendationResponse.session_id` 是否返回给上游

示例请求：
```json
{
  "query": "我想做一个高并发RAG系统",
  "timeout": 60,
  "max_iterations": 3,
  "session_id": "abc123"
}
```

### 5.2 人工确认恢复接口（HITL）：`POST /api/v1/recommend/confirm`

接口入口：
- `fastapi_demo/app/api/v1/routes.py:370`
- 函数：`confirm_software_recommendation(request_data: RecommendationConfirmRequest)`

入参模型：
- `fastapi_demo/app/api/v1/models.py:31`
- 类：`RecommendationConfirmRequest`
- 关键字段：`session_id`、`action(confirm/edit)`、`sub_questions`、`comment`

函数行为：
1. 使用相同 `thread_id=session_id` 续跑图
2. 用 `Command(resume=...)` 把人工确认结果塞回图
3. 若再次中断继续返回 `awaiting_human_confirmation`
4. 若完成则返回最终答案并追加 assistant 消息进 session history

关键依赖函数：
- `run_agent_async`（`routes.py:437`）
- `_extract_interrupt_payload`（`routes.py:144`）
- `_append_session_messages`（`routes.py:247`）

### 5.3 会话状态接口

#### 5.3.1 `GET /api/v1/session-state/{session_id}`
- 入口：`fastapi_demo/app/api/v1/routes.py:418`
- 函数：`get_session_state(session_id: str)`
- 功能：返回 `facts` + `updated_at`（响应模型 `SessionStateResponse`）
- 依赖：`_get_or_create_session_state`（`routes.py:210`）

#### 5.3.2 `PUT /api/v1/session-state/{session_id}`
- 入口：`fastapi_demo/app/api/v1/routes.py:428`
- 函数：`upsert_session_state(session_id: str, request_data: SessionStateUpdateRequest)`
- 功能：合并写入 facts，不直接覆盖整个 session 对象
- 依赖：`_merge_session_facts`（`routes.py:228`）
- 入参模型：`SessionStateUpdateRequest`（`models.py:75`）

### 5.4 初始化向量库：`POST /api/v1/initialize-db`
- 入口：`fastapi_demo/app/api/v1/routes.py:448`
- 函数：`initialize_database()`
- 功能：写入示例文档到向量库
- 依赖：`initialize_vector_store`（`fastapi_demo/software_recommend_system/utils.py:13`）

### 5.5 Chroma 向量库与 Embedding 调用链（你问的重点）

配置入口：
- `fastapi_demo/software_recommend_system/config.py:18`：`CHROMA_DB_PATH`
- `fastapi_demo/software_recommend_system/config.py:19`：`EMBEDDING_MODEL`
- `fastapi_demo/software_recommend_system/config.py:20`：`CHUNK_SIZE`
- `fastapi_demo/software_recommend_system/config.py:21`：`CHUNK_OVERLAP`
- `fastapi_demo/app/core/config.py:26`：`CHROMA_DB_PATH`
- `fastapi_demo/app/core/config.py:27`：`EMBEDDING_MODEL`
- `fastapi_demo/app/core/config.py:28`：`CHUNK_SIZE`
- `fastapi_demo/app/core/config.py:29`：`CHUNK_OVERLAP`

向量写入链路（初始化）：
1. `POST /api/v1/initialize-db` 进入 `initialize_database`  
位置：`fastapi_demo/app/api/v1/routes.py:448`
2. 在路由里构造 `sample_docs`（每条含 `id + content + metadata`）  
位置：`fastapi_demo/app/api/v1/routes.py:451`
3. 调用 `initialize_vector_store(sample_docs)`  
位置：`fastapi_demo/app/api/v1/routes.py:489`
4. 进入 ingest 编排入口 `initialize_vector_store`  
位置：`fastapi_demo/software_recommend_system/utils.py:13`
5. 文档标准化：`normalize_documents`  
位置：`fastapi_demo/software_recommend_system/ingestion/loader.py:4`
6. 文本切块：`chunk_documents(..., chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)`  
位置：`fastapi_demo/software_recommend_system/ingestion/chunker.py:166`  
实现要点：优先按中英文标点做句子切分，再按 `chunk_size` 组块；若单句超长，回退到固定长度切分，并保留 overlap
7. 文本向量化：`embed_texts(chunk_texts)`  
位置：`fastapi_demo/software_recommend_system/ingestion/embedder.py:14`  
代码行为：`client.embeddings.create(model=settings.EMBEDDING_MODEL, input=...)`
8. Chroma 写入：`index_embeddings(...)`  
位置：`fastapi_demo/software_recommend_system/ingestion/indexer.py:16`  
代码行为：`collection.upsert(ids, documents, metadatas, embeddings)`
9. Chroma 持久化路径读取：`get_chroma_collection`  
位置：`fastapi_demo/software_recommend_system/ingestion/indexer.py:10`  
代码行为：`chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)`

向量检索链路（查询）：
1. 节点检索最终会调用 `unified_search`  
位置：`fastapi_demo/software_recommend_system/tools.py:245`
2. `unified_search` 先调 `similarity_search`（向量检索）  
位置：`fastapi_demo/software_recommend_system/tools.py:20`
3. `similarity_search` 用同一个 `CHROMA_DB_PATH` 打开持久化库并读取 `software_recommendations` 集合
4. 对 query 做 embedding（单条）  
位置：`fastapi_demo/software_recommend_system/tools.py:34`（`embed_texts([query])`）
5. 调用 `collection.query(query_embeddings=..., n_results=k)` 返回相似文档
6. 若 Tavily 可用，`unified_search` 会把网络搜索结果拼接进来（不是向量库结果）

### 5.6 chunk 向量化当前状态（已实现）

当前代码现状：
- 已实现“先 chunk 再 embedding 再入库”的模块化流水线。
- chunk 在 `fastapi_demo/software_recommend_system/ingestion/chunker.py:166` 执行，采用“句子优先”切分：
  - 先用中英文标点拆句（`_split_into_sentence_spans`，`chunker.py:39`）
  - 再按 `chunk_size` 进行句子分组（`_build_chunks`，`chunker.py:117`）
  - 若单句超过 `chunk_size`，回退固定长度切分（`_split_span_by_fixed_length`，`chunker.py:49`）
  - overlap 按句子尾部复用逻辑保留
- 每个 chunk metadata 会写入：
  - `source_doc_id`
  - `chunk_index`
  - `chunk_start`
  - `chunk_end`
- embedding 调用统一在 `fastapi_demo/software_recommend_system/ingestion/embedder.py:14`，项目内不再重复实现 embedding API 调用。

如果你要调优 chunk 质量（现在就能改）：
1. 改环境变量 `CHUNK_SIZE`、`CHUNK_OVERLAP`。  
2. 或直接改 `chunk_documents` 句子分组策略（例如引入 token 长度预算）。  
3. 若要接入 token splitter，可替换 `_split_into_sentence_spans` 或 `_build_chunks` 的实现。  

### 5.7 启动自动入库（Startup Ingest）机制

目标：
- FastAPI 启动时自动扫描目录并入库（支持 `.txt/.md/.pdf`）。
- 仅当向量库为空时执行，避免每次启动重复导入。

入口链路：
1. FastAPI 启动事件  
位置：`fastapi_demo/app/main.py`（`app.on_event("startup")(startup_event_handler)`）
2. 启动钩子执行  
位置：`fastapi_demo/app/core/startup.py:8`（`startup_event_handler`）
3. 调用自动入库  
位置：`fastapi_demo/app/core/startup.py:16`（`run_startup_ingestion_if_needed()`）
4. 自动入库主逻辑  
位置：`fastapi_demo/app/api/v1/startup_ingest.py:120`

自动入库逻辑细节（`startup_ingest.py`）：
- 扫描文件后缀：`SUPPORTED_EXTENSIONS`（`startup_ingest.py:15`）
- 扫描目录：`settings.INGEST_PATH`（配置在 `app/core/config.py:30`）
- 向量库空库判断：`collection.count()`（`startup_ingest.py:126`）
  - `count > 0` 直接跳过自动入库
- 文件读取：
  - 文本：`_read_plain_text`（`startup_ingest.py:23`）
  - PDF：`_read_pdf_text`（`startup_ingest.py:34`，通过 `PyPDF2`）
- 文件列表收集：`_list_candidate_files`（`startup_ingest.py:63`）
- 单文档入库：`_ingest_single_document`（`startup_ingest.py:87`）
  - 内部依次调用：`normalize_documents -> chunk_documents -> embed_texts -> index_embeddings`

注意：
- 这个自动入库不改任何现有 REST 路由，只在启动阶段工作。
- 如果未安装 `PyPDF2`，PDF 文件会被跳过并打 warning 日志。

---

## 6. 会话记忆怎么存（函数级别）

### 6.1 记忆层次

1. Session 状态层（facts + messages）
- 代码：`fastapi_demo/app/api/v1/routes.py`
- 默认落盘：`.runtime/fastapi_session_state.json`
- 核心变量：`SESSION_STATE_STORE`（`routes.py:135`）

2. LangGraph checkpoint 层（图恢复）
- 代码：`fastapi_demo/software_recommend_system/rag_agent.py`
- 默认落盘：`.runtime/langgraph_checkpoints.sqlite`
- 关键函数：`_build_checkpointer`（`rag_agent.py:32`）

### 6.2 Session 状态相关函数索引（`routes.py`）

| 函数 | 行号 | 功能 |
|---|---|---|
| `_normalize_session_facts` | 58 | 清洗 facts，保证 key/value 是可用字符串 |
| `_normalize_session_messages` | 70 | 清洗 messages，仅保留 `system/user/assistant`，并按上限截断 |
| `_normalize_updated_at` | 86 | 把时间字段统一成 float |
| `_normalize_session_state` | 93 | 把一整条 session 状态转成规范结构 |
| `_persist_session_state_store_locked` | 102 | 将内存 session store 写到 json 文件 |
| `_load_session_state_store` | 110 | 服务启动时从 json 恢复 session store |
| `_get_or_create_session_state` | 210 | 读取某个 session，不存在就创建 |
| `_merge_session_facts` | 228 | 合并事实，带 `user_name` 合法性校验 |
| `_append_session_messages` | 247 | 合并消息历史并更新 `updated_at` |
| `_upsert_name_fact_from_query` | 262 | 从 query 中提取姓名并写入 `facts.user_name` |

### 6.3 身份识别相关函数索引（`routes.py`）

| 函数 | 行号 | 功能 |
|---|---|---|
| `_extract_name_fact` | 159 | 从“我是X / 我叫X / my name is X”提取姓名 |
| `_normalize_candidate_name` | 184 | 清洗姓名候选值，过滤无效词（如“什么”） |
| `_is_asking_user_name` | 198 | 识别“我叫什么/what is my name”类问句 |

### 6.4 排查“上下文丢失”步骤

1. 看请求里 `session_id` 是否每轮一致  
2. 调 `GET /api/v1/session-state/{session_id}` 看 `facts` 是否正确  
3. 查看 `.runtime/fastapi_session_state.json` 是否有该 session 且 `updated_at` 递增  
4. 查看 `.runtime/langgraph_checkpoints.sqlite` 是否在更新  
5. 确认 `routes.py:270` 构造 `AgentState` 时 `messages` 非空（若历史存在）

---

## 7. RAG 图节点（精确到每个函数）

文件：`fastapi_demo/software_recommend_system/nodes.py`

### 7.1 节点前置工具函数

| 函数 | 行号 | 作用 |
|---|---|---|
| `_get_field` | 18 | 统一从 `dict/object` 读取字段 |
| `_set_field` | 23 | 统一向 `dict/object` 写字段 |
| `_get_openai_client` | 30 | 初始化 LLM 客户端（读取 `settings`） |
| `_is_drawing_request` | 105 | 判断是否是绘图请求 |
| `_extract_current_user_query` | 141 | 从 `user_query` 中剥离 `[Known User Facts]` 注入段，只保留“本轮用户原始输入”用于路由 |
| `_contains_tech_term` | 150 | 技术词边界匹配，避免短词误命中 |
| `_collect_tech_hits` | 159 | 统计命中的技术词，用于 RAG 判定 |
| `_is_chat_first_query` | 169 | 判断是否属于闲聊/身份类优先走 Chat |
| `_has_rag_intent` | 175 | 判断是否存在明显技术咨询意图 |
| `_normalize_sub_questions` | 413 | 子问题清洗，保证为字符串数组 |

### 7.2 主流程节点（按图执行顺序）

| 节点函数 | 行号 | 输入关键信息 | 输出关键信息 | 作用 |
|---|---|---|---|---|
| `entry_node` | 181 | `state.user_query` | `messages`, `start_time` | 记录本轮输入并初始化时间 |
| `query_normalization_node` | 226 | `user_query` | `normalized_query`, `constraints` | 问题规范化/约束提取 |
| `routing_node` | 194 | `user_query` | `mode` | 分流到 `rag/chat/draw` |
| `sub_question_generation_node` | 341 | `normalized_query`, `constraints` | `sub_questions` | 拆分子问题 |
| `human_confirmation_node` | 426 | `sub_questions` | `sub_questions`, `human_feedback` | 人工确认中断与恢复 |
| `evidence_collection_node` | 477 | `sub_questions` | `evidence`, `iteration_count+1` | 检索证据 |
| `evidence_evaluation_node` | 507 | `evidence` | 更新 `quality_score` | 评估证据质量 |
| `candidate_generation_node` | 528 | `evidence` | `candidates` | 生成候选方案 |
| `coverage_check_node` | 651 | `sub_questions/evidence/candidates` | `coverage`, `needs_refinement` | 判断是否继续迭代 |
| `answer_generation_node` | 682 | `candidates`, `user_query` | `final_answer`, `messages` | RAG 分支最终答复 |
| `chat_answer_generation_node` | 714 | `messages` | `final_answer`, `messages` | Chat 分支答复 |
| `pre_drawing_node` | 755 | `user_query` | `drawing_params` | 绘图参数准备 |
| `draw_image_node` | 761 | `drawing_params` | `image_result`, `final_answer` | 绘图执行 |

### 7.2.1 `routing_node` 具体判定规则（修复后）

1. 先取用于路由的文本：`routing_query = _extract_current_user_query(state.user_query)`，避免 `[Known User Facts]` 影响分流。  
2. 若命中 `_is_drawing_request(routing_query)`，直接 `mode="draw"`。  
3. 若命中 `_is_chat_first_query(routing_query)` 且不命中 `_has_rag_intent(routing_query)`，走 `mode="chat"`。  
4. 否则统计 `tech_hits = _collect_tech_hits(routing_query)`：  
   - 命中 `_has_rag_intent` -> `rag`  
   - `tech_hits >= 2` -> `rag`  
   - `tech_hits == 1` 且不是短歧义词（`go/ci/cd`）-> `rag`  
   - 其余 -> `chat`

### 7.3 图编排入口（不是 nodes.py 但必须一起看）

文件：`fastapi_demo/software_recommend_system/rag_agent.py`

| 函数 | 行号 | 功能 |
|---|---|---|
| `_build_checkpointer` | 32 | 初始化 sqlite/memory checkpointer |
| `create_rag_with_routing_agent` | 52 | 构建完整 `rag/chat/draw` 路由图 |
| `create_rag_agent` | 152 | 构建纯 RAG 图（备用） |

---

## 8. 新同学常见需求 -> 精确修改位置

### 场景 A：改 API 参数或返回字段

先改模型：
- `fastapi_demo/app/api/v1/models.py`
- `RecommendationRequest`（6）
- `RecommendationConfirmRequest`（31）
- `RecommendationResponse`（54）
- `SessionStateUpdateRequest`（75）
- `SessionStateResponse`（79）

再改路由：
- `fastapi_demo/app/api/v1/routes.py`
- `get_software_recommendation`（270）
- `confirm_software_recommendation`（370）

### 场景 B：改“我是谁”记忆策略

核心文件：
- `fastapi_demo/app/api/v1/routes.py`

核心函数（按链路顺序）：
- `_extract_name_fact`（159）  
从用户输入中抽取姓名（支持“我是X / 我叫X / my name is X”）
- `_normalize_candidate_name`（184）  
过滤无效值（例如“什么/谁/name/what”等）
- `_is_asking_user_name`（198）  
识别“我是谁 / what is my name”类问题
- `_merge_session_facts`（228）  
将抽取到的 `user_name` 合并写入 session facts
- `_upsert_name_fact_from_query`（262）  
在推荐入口请求一开始触发抽取+写入

请求入口关键位置：
- `get_software_recommendation`（270）先调用 `_upsert_name_fact_from_query`。
- 若已知姓名且用户在问“我是谁”，在 285 处直接走记忆短路回答。
- 305-311 会把已知事实拼到 `effective_query`。注意：路由层已在 `nodes.py:_extract_current_user_query` 中剥离这一段，避免误判为 RAG。

如果你要改身份记忆策略：
1. 先改 `_extract_name_fact` 的正则覆盖面。
2. 再改 `_normalize_candidate_name` 的过滤规则。
3. 最后确认 `_merge_session_facts` 不会把无效值写回。

### 场景 C：改推荐效果（RAG 质量）

节点层：
- `fastapi_demo/software_recommend_system/nodes.py`
- 优先看：
  - `routing_node`（194）：先决定 `rag/chat/draw`
  - `query_normalization_node`（226）：查询规范化
  - `sub_question_generation_node`（341）：问题拆分
  - `evidence_collection_node`（477）：证据检索
  - `candidate_generation_node`（528）：候选方案生成
  - `answer_generation_node`（682）：最终回答生成

工具层：
- `fastapi_demo/software_recommend_system/tools.py`
- `similarity_search`（20）、`_tavily_search`（94）、`unified_search`（245）

如果你要改“分块后再向量化（chunk embedding）”：
- 主改目录：`fastapi_demo/software_recommend_system/ingestion/`
- 关键函数：
  - `normalize_documents`（`loader.py:4`）：原始文档标准化
  - `chunk_documents`（`chunker.py:166`）：句子优先切块策略
  - `_split_into_sentence_spans`（`chunker.py:39`）：句子切分
  - `_build_chunks`（`chunker.py:117`）：句子组块 + overlap
  - `embed_texts`（`embedder.py:14`）：唯一 embedding API 调用点
  - `index_embeddings`（`indexer.py:16`）：Chroma upsert 写入点
  - `initialize_vector_store`（`utils.py:13`）：总编排入口
- 配置同步：
  - `fastapi_demo/software_recommend_system/config.py`：`CHUNK_SIZE/CHUNK_OVERLAP`（20/21）
  - `fastapi_demo/app/core/config.py`：`CHUNK_SIZE/CHUNK_OVERLAP`（28/29）

### 场景 D：改流程图（先做什么后做什么）

文件：
- `fastapi_demo/software_recommend_system/rag_agent.py`

重点函数与位置：
- `create_rag_with_routing_agent`（52）：主图定义入口
- 内部 `route_based_on_mode`（79）：读取 `state.mode` 返回 `rag/chat/draw`
- `add_conditional_edges("routing", ...)`（89）：把模式映射到具体分支节点
  - `rag -> sub_question_generation`
  - `chat -> chat_answer_generation`
- `draw -> pre_drawing`
- 内部 `should_continue`（107）+ `add_conditional_edges("coverage_check", ...)`（134）：控制 RAG 迭代继续/终止

说明：当前“全部走 RAG”的问题不是图边配置错，而是路由判定逻辑偏向 RAG；图条件边本身是正确的。

### 场景 E：改配置（模型、端口、向量库等）

FastAPI 层配置：
- `fastapi_demo/app/core/config.py`
- 类：`Settings`（9）

业务层配置：
- `fastapi_demo/software_recommend_system/config.py`
- 类：`Settings`（8）

高频环境变量：
- `SERVER_HOST`、`SERVER_PORT`、`DEBUG`
- `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`
- `CHROMA_DB_PATH`、`EMBEDDING_MODEL`、`CHUNK_SIZE`、`CHUNK_OVERLAP`
- `INGEST_PATH`（启动自动扫描目录）
- `TIMEOUT_BUDGET`、`MAX_ITERATIONS`

### 场景 F：改“启动自动入库”策略

核心文件：
- `fastapi_demo/app/core/startup.py`
- `fastapi_demo/app/api/v1/startup_ingest.py`

关键位置：
- `startup_event_handler`（`startup.py:8`）：启动时触发入口
- `run_startup_ingestion_if_needed`（`startup_ingest.py:120`）：空库判断 + 扫描 + 入库主流程
- `_list_candidate_files`（`startup_ingest.py:63`）：扫描目录与扩展名过滤
- `_read_pdf_text`（`startup_ingest.py:34`）：PDF 提取文本
- `_ingest_single_document`（`startup_ingest.py:87`）：调用现有 ingestion 模块链路

你通常会改：
1. 扫描目录：`INGEST_PATH`
2. 支持的扩展名：`SUPPORTED_EXTENSIONS`
3. 空库判断策略：`collection.count() == 0` 的条件逻辑

---

## 9. 最小调试清单（卡住就按这个查）

1. 服务是否启动成功：`/health` 是否 200
2. 请求是否带了 `session_id`（连续对话必须固定）
3. `/api/v1/session-state/{session_id}` 读出来的 `facts` 是否正确
4. `.runtime/fastapi_session_state.json` 是否写入
5. `.runtime/langgraph_checkpoints.sqlite` 是否创建/更新
6. 422 校验报错先看 `app/main.py` 的 validation log
7. 推荐效果差优先看 `nodes.py` 的路由和 evidence 相关节点
8. 启动自动入库是否触发：看 `startup_ingest` 相关日志
9. `INGEST_PATH` 目录是否存在且有 `.txt/.md/.pdf` 文件
10. 若预期自动入库却未执行，先看向量库 `collection.count()` 是否已大于 0（非空会跳过）

---

## 10. 建议阅读顺序（半天上手）

1. `fastapi_demo/app/main.py`
2. `fastapi_demo/app/core/config.py`
3. `fastapi_demo/app/core/startup.py`
4. `fastapi_demo/app/api/v1/startup_ingest.py`
5. `fastapi_demo/app/api/v1/models.py`
6. `fastapi_demo/app/api/v1/routes.py`
7. `fastapi_demo/software_recommend_system/state.py`
8. `fastapi_demo/software_recommend_system/rag_agent.py`
9. `fastapi_demo/software_recommend_system/nodes.py`
10. `fastapi_demo/software_recommend_system/tools.py`
11. `fastapi_demo/software_recommend_system/ingestion/loader.py`
12. `fastapi_demo/software_recommend_system/ingestion/chunker.py`
13. `fastapi_demo/software_recommend_system/ingestion/embedder.py`
14. `fastapi_demo/software_recommend_system/ingestion/indexer.py`

看完这 14 个文件，基本就能独立改需求了。


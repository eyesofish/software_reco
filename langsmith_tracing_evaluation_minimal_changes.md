# LangSmith Tracing + Evaluation 最小改造说明（已落地）

更新时间：2026-03-04  
项目路径：`D:\Github\software_reco`

---

## 1. 一句话结论

本次已经按“最小侵入”完成：

1. LangSmith tracing 接入（`wrap_openai` + `@traceable`）。
2. chat/rag 统一检索入口（共享 `retrieve()`）。
3. pipeline 输出评估字段（`hitl`、`retrieval_records`、`retrieved_doc_ids`）。
4. 新增可直接跑的 evaluate 脚本（`routing_accuracy` / `retrieval_recall` / `answer_correctness`）。

---

## 2. Retriever 是否共享组件？

是，已经是共享组件。

当前调用关系：

```text
chat pipeline
  -> retrieve(query)

rag pipeline
  -> retrieve(sub_query)

retrieve()
  -> similarity_search()
  -> _tavily_search()
  -> merge + normalize
```

兼容性也保留了：`unified_search()` 仍可用，但内部转调 `retrieve()`。

---

## 3. 改了哪些地方（精确到文件/函数）

## 3.1 新增观测适配层（LangSmith 可选依赖，不装也不崩）

- 文件：`fastapi_demo/software_recommend_system/observability.py`
- 新增：
  - `wrap_openai(client)`
  - `traceable(...)`
- 作用：
  - 安装了 `langsmith`：自动启用 tracing
  - 未安装：自动 no-op，不影响现有逻辑

---

## 3.2 共享检索组件接入 tracing + 标准化 metadata

- 文件：`fastapi_demo/software_recommend_system/retriever.py`
- 函数：
  - `retrieve(query: str, top_k: int = 5) -> List[Document]`
  - `_normalize_documents(...)`
- 改动：
  - `@traceable(name="retrieve_shared")`
  - 每个 `Document.metadata` 统一补齐：
    - `doc_id`
    - `source`
    - `retrieval_source` (`vector` / `web`)
    - `score`

---

## 3.3 文档结构字段扩展

- 文件：`fastapi_demo/software_recommend_system/document_schema.py`
- 模型：`Metadata`
- 改动：
  - 新增 `retrieval_source: Optional[str] = None`

---

## 3.4 OpenAI 调用统一包裹 tracing

- 文件：`fastapi_demo/software_recommend_system/tools.py`
  - `_get_openai_client()` -> `wrap_openai(openai.OpenAI(...))`
- 文件：`fastapi_demo/software_recommend_system/ingestion/embedder.py`
  - `_get_openai_client(...)` -> `wrap_openai(openai.OpenAI(...))`
- 文件：`fastapi_demo/software_recommend_system/nodes.py`
  - `_get_openai_client()` -> `wrap_openai(openai.OpenAI(...))`

---

## 3.5 核心节点 tracing + eval 字段透传

- 文件：`fastapi_demo/software_recommend_system/nodes.py`
- 增加 `@traceable`：
  - `routing_node` (`routing_decision`)
  - `query_normalization_node`
  - `sub_question_generation_node` (`task_decomposition`)
  - `human_confirmation_node` (`hitl_confirmation`)
  - `retrieve_node` (`retrieve_multi_subquery`)
  - `answer_generation_node` (`final_generation_rag`)
  - `chat_answer_generation_node` (`final_generation_chat`)

- 增加检索评估聚合：
  - `_build_retrieval_record(...)`
  - `_merge_doc_ids(...)`
  - `_extract_doc_id(...)`

- `retrieve_node` 输出新增：
  - `retrieval_records`（每个 subquery 一条）
  - `retrieved_doc_ids`（全局去重 union）

- `chat_answer_generation_node` 也输出：
  - 用伪子问题 `subquery_id="chat_0"` 记录检索
  - 同样返回 `retrieval_records` + `retrieved_doc_ids`

- HITL 策略扩展（保持原逻辑默认不变）：
  - `human`（默认）
  - `auto_confirm`
  - `oracle_edit`
  - 并在 state 中写入 `hitl = {policy, decision, edited_subqueries}`

---

## 3.6 AgentState 扩字段

- 文件：`fastapi_demo/software_recommend_system/state.py`
- 新增字段：
  - `retrieval_records`
  - `retrieved_doc_ids`
  - `hitl_policy`
  - `oracle_edits`
  - `hitl`

---

## 3.7 API 入参与出参补齐评估字段

- 文件：`fastapi_demo/app/api/v1/models.py`
- `RecommendationRequest` 新增：
  - `hitl_policy` (`human|auto_confirm|oracle_edit`)
  - `oracle_edits`
- `RecommendationResponse` 新增：
  - `hitl`
  - `retrieval_records`
  - `retrieved_doc_ids`

- 文件：`fastapi_demo/app/api/v1/routes.py`
  - 新增 `_extract_eval_payload(...)`
  - `_execute_recommend_turn` 增加 `@traceable(name="api_recommend_turn")`
  - `run_agent_async` 增加 `@traceable(name="api_agent_invoke")`
  - 将 request 中 `hitl_policy/oracle_edits` 透传到 `AgentState`
  - 在 `/recommend` 和 `/recommend/confirm` 各返回路径统一回传：
    - `hitl`
    - `retrieval_records`
    - `retrieved_doc_ids`

---

## 3.8 evaluate 脚本新增（可直接运行）

- 新目录：`fastapi_demo/evaluation/`
- 新文件：
  - `fastapi_demo/evaluation/langsmith_evaluate.py`
  - `fastapi_demo/evaluation/__init__.py`

脚本提供：

1. 两个 target：
  - `run_pipeline_auto_confirm`
  - `run_pipeline_oracle_edit`
2. 三个 evaluator：
  - `routing_accuracy`
  - `retrieval_recall`
  - `answer_correctness`
3. CLI 参数：
  - `--dataset`
  - `--policy auto_confirm|oracle_edit`
  - `--prefix`
  - `--max-concurrency`

---

## 4. 当前输出契约（evaluate 直接可用）

pipeline 最终输出已满足：

```json
{
  "final_answer": "...",
  "mode": "rag",
  "hitl": {
    "policy": "auto_confirm",
    "decision": "confirm",
    "edited_subqueries": []
  },
  "retrieval_records": [
    {
      "subquery_id": "sq_1",
      "subquery": "...",
      "retrieved_doc_ids": ["doc_1", "doc_2"],
      "retrieved_contexts": ["..."]
    }
  ],
  "retrieved_doc_ids": ["doc_1", "doc_2", "doc_9"]
}
```

---

## 5. 傻瓜教程：一步步跑 evaluate

下面是 Windows PowerShell 版，按顺序执行即可。

## Step 0：进入项目

```powershell
cd D:\Github\software_reco\fastapi_demo
```

## Step 1：安装依赖

```powershell
pip install -r .\software_recommend_system\requirements.txt
```

如果你用的是 conda Python，也可以显式指定：

```powershell
C:\ProgramData\anaconda3\python.exe -m pip install -r .\software_recommend_system\requirements.txt
```

## Step 2：配置环境变量

```powershell
$env:LANGSMITH_API_KEY="lsv2_xxx"
$env:LANGSMITH_TRACING="true"
$env:LANGSMITH_PROJECT="software-reco-rag-eval"

# 评测裁判模型（answer_correctness）要用
$env:OPENAI_API_KEY="sk-xxx"
# 如果你走兼容网关（比如 DashScope/OpenAI-compatible），再配这个
# $env:OPENAI_BASE_URL="https://xxx/v1"
```

可选（不设默认 `gpt-4o-mini`）：

```powershell
$env:EVAL_JUDGE_MODEL="gpt-4o-mini"
```

## Step 3：在 LangSmith 准备 dataset

Dataset 每条至少包含：

```json
{
  "inputs": {
    "question": "你的问题",
    "oracle_edits": ["可选，oracle_edit 模式用"]
  },
  "outputs": {
    "reference_answer": "标准答案",
    "gold_doc_ids": ["doc_a", "doc_b"],
    "expected_mode": "rag"
  },
  "metadata": {
    "split": "test"
  }
}
```

字段含义：

1. `expected_mode`：给 `routing_accuracy` 用。
2. `gold_doc_ids`：给 `retrieval_recall` 用。
3. `reference_answer`：给 `answer_correctness` 用。
4. `oracle_edits`：只在 `--policy oracle_edit` 生效。

## Step 4：先跑 baseline（auto_confirm）

```powershell
C:\ProgramData\anaconda3\python.exe -m evaluation.langsmith_evaluate `
  --dataset rag_hitl_eval_v1 `
  --policy auto_confirm `
  --prefix rag-hitl-auto-confirm `
  --max-concurrency 4
```

## Step 5：再跑上界（oracle_edit）

```powershell
C:\ProgramData\anaconda3\python.exe -m evaluation.langsmith_evaluate `
  --dataset rag_hitl_eval_v1 `
  --policy oracle_edit `
  --prefix rag-hitl-oracle-edit `
  --max-concurrency 4
```

## Step 6：在 LangSmith 看结果

重点看：

1. `routing_accuracy`
2. `retrieval_recall`
3. `answer_correctness`

同时比较：

`oracle_edit - auto_confirm` 的 correctness 提升（就是 HITL 价值）。

---

## 6. 常见问题（直接对照排查）

## Q1: 看不到 trace？

检查 3 个环境变量是否真的生效：

1. `LANGSMITH_API_KEY`
2. `LANGSMITH_TRACING=true`
3. `LANGSMITH_PROJECT`

## Q2: answer_correctness 是空？

`OPENAI_API_KEY` 没配，或 judge 模型不可用。  
脚本会返回 `score=None` 并给 comment。

## Q3: retrieval_recall 很低？

先确认 `gold_doc_ids` 是否和系统产出的 `retrieved_doc_ids` 使用同一套 ID 规则。  
当前系统会优先使用 `doc_id/source_doc_id/url`，否则回退到稳定 hash ID。

## Q4: 评测时还触发人工确认中断？

确认你传的是：

1. `--policy auto_confirm` 或
2. `--policy oracle_edit`

这两种不会走真实人工中断。

---

## 7. 最小使用建议（推荐顺序）

1. 先用 20 条小样本跑 `auto_confirm`，确认链路通。
2. 再跑同一批数据的 `oracle_edit`，看 correctness 增益。
3. 最后扩大到完整测试集，并固定 `dataset + prefix` 命名规范。


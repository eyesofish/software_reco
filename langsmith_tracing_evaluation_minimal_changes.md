# LangSmith 评测傻瓜教程（token + doc_id 变更后复测版）

适用目录：`D:\Github\software_reco\fastapi_demo`  
适用环境：Windows PowerShell + conda `dech2`

这份文档只做一件事：让你从 0 到 1 完整跑通一轮真实评测（完整入库 + 20+ 样本）。

---

## 0. 先决条件（只看这 4 条）

1. 本地 LLM 服务可启动：`http://127.0.0.1:18000/v1`
2. FastAPI 可启动：`http://127.0.0.1:8000`
3. `fastapi_demo/.env` 里有 `LANGSMITH_API_KEY`
4. 你在 conda 环境 `dech2`

---

## 1. 进入项目 + 激活环境

```powershell
cd D:\Github\software_reco\fastapi_demo
conda activate dech2
```

---

## 1.5 推荐给 Codex 的一键执行（优先）

如果你是要直接“丢给 Codex 执行”，优先跑这条：

```powershell
cd D:\Github\software_reco\fastapi_demo
conda activate dech2
powershell -ExecutionPolicy Bypass -File .\evaluation\run_e2e_real_eval.ps1
```

成功后会输出 `REPORT_PATH=...`，报告统一落在：
`D:\Github\software_reco\fastapi_demo\evaluation\reports\`

> 下面第 2~11 节是手工分步版（用于排错和人工确认每一步）。

---

## 2. 启动本地 LLM（已改为 6000 上下文）

```powershell
wsl -d Ubuntu -- python3 /mnt/c/Users/UYou2/.runtime/start_vllm_wsl.py
```

等待模型就绪并检查 `max_model_len`：

```powershell
for($i=0;$i -lt 240;$i++){
  try {
    $m = Invoke-RestMethod -Uri "http://127.0.0.1:18000/v1/models" -Method Get -TimeoutSec 3
    $m.data | Select-Object id,max_model_len
    break
  } catch {
    Start-Sleep -Seconds 2
  }
}
```

成功标准：`max_model_len = 6000`。

---

## 3. 导出 TechQA 文本到 ingest_docs

```powershell
python -m evaluation.export_techqa_contexts_to_ingest_docs `
  --hf-dataset nvidia/TechQA-RAG-Eval `
  --hf-split test `
  --output-dir ingest_docs `
  --clean-output `
  --overwrite-existing
```

说明：

1. 脚本会自动把不存在的 `test` split 回退到 `train`。
2. 会生成 `ingest_docs/_techqa_context_export_manifest.json`。

---

## 4. 清空向量库（doc_id 或 embedding 改动后必须做）

```powershell
@'
import chromadb
client = chromadb.PersistentClient(path=r"D:/Github/software_reco/fastapi_demo/chroma_db")
for c in client.list_collections():
    client.delete_collection(c.name)
print("cleared")
'@ | python -
```

---

## 5. 启动 FastAPI（触发启动自动入库）

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

另开一个终端做健康检查：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
```

检查当前向量数（应 > 0）：

```powershell
@'
import chromadb
client = chromadb.PersistentClient(path=r"D:/Github/software_reco/fastapi_demo/chroma_db")
col = client.get_or_create_collection("software_recommendations")
print(col.count())
'@ | python -
```

---

## 6. 创建 LangSmith 评测数据集（25 样本示例）

```powershell
python -m evaluation.convert_hf_to_langsmith `
  --hf-dataset nvidia/TechQA-RAG-Eval `
  --hf-split test `
  --langsmith-dataset rag_hitl_eval_real_25 `
  --max-samples 25 `
  --overwrite
```

成功标准：

1. 输出 `converted=25`
2. 输出 `created=25`
3. 预览文件在 `evaluation/previews/` 下

---

## 7. 跑 evaluate（手工版；完整一轮）

```powershell
$prefix = "rag-hitl-real-" + (Get-Date -Format "yyyyMMdd-HHmmss")
python -m evaluation.langsmith_evaluate `
  --dataset rag_hitl_eval_real_25 `
  --policy auto_confirm `
  --prefix $prefix `
  --max-concurrency 1
```

成功标准：

1. 命令退出码 0
2. 输出里有 LangSmith URL
3. URL 里有 `selectedSessions=<project_id>`

补充：如果你用的是 `run_e2e_real_eval.ps1`，它会自动完成第 3~8 节并生成 markdown 报告到 `evaluation/reports/`。

---

## 8. 拉取本轮聚合指标（routing/recall/correctness）

把下面的 `YOUR_PROJECT_ID` 换成上一步 URL 的 `selectedSessions`：

```powershell
@'
import json
from pathlib import Path
from dotenv import load_dotenv
from langsmith import Client

project_id = "YOUR_PROJECT_ID"
load_dotenv(Path(r"D:/Github/software_reco/fastapi_demo/.env"))
client = Client()
res = client.get_experiment_results(project_id=project_id)
feedback = res.get("feedback_stats", {}) or {}
run_stats = res.get("run_stats", {}) or {}

for k in ["routing_accuracy", "retrieval_recall", "answer_correctness"]:
    v = feedback.get(k, {}) or {}
    print(k, {"n": v.get("n"), "avg": v.get("avg"), "stdev": v.get("stdev")})

print("run_count", run_stats.get("run_count"))
print("latency_p50", run_stats.get("latency_p50"))
print("total_tokens", run_stats.get("total_tokens"))
'@ | python -
```

---

## 9. 两个最关键排错（你这次真实遇到的）

### 9.1 历史报错：`2049 > 2048`（上下文超限）

说明：

1. 这条报错对应旧的 2048 上下文配置。
2. 你当前已经改成 6000，上线基线应是：`max_model_len=6000`。

处理：

1. 确认启动脚本是：`C:\Users\UYou2\.runtime\start_vllm_wsl.py`（该脚本里是 `--max-model-len 6000`）。
2. 确认 `/v1/models` 返回 `max_model_len=6000`。
3. 如果仍看到 `2049 > 2048`，通常是连到了旧进程/旧端口，先停掉旧 vLLM 再按第 2 节重启。
4. 若模型已是 6000 但仍有超限/降级，检查应用侧窗口参数（`CHAT_MAX_INPUT_TOKENS`、`CHAT_MAX_OUTPUT_TOKENS`、`CHAT_MAX_MESSAGES`），以及候选生成阶段输入是否过长。

### 9.2 报错：`Collection expecting embedding with dimension of 512, got 1024`

本质：入库和检索用了不同 embedding 维度。

必须保证：

1. 入库用什么 embedding，评测检索就必须用同一个。
2. 修改 embedding 配置后，必须清空 `chroma_db` 并重新入库。

### 9.3 `retrieval_recall` 一直是 0（doc_id 对不齐）

处理要点：

1. 先确认你已经做了第 4 节“清空向量库 + 重新入库”。
2. 确认评测集 `gold_doc_ids` 与运行输出 `retrieved_doc_ids` 命名空间一致（建议都对齐到文件名，如 `swg21996508.txt`）。
3. 若代码刚改过 `doc_id` 逻辑，但未重建向量库，当前库里 metadata 仍是旧值，recall 会被压成 0。

---

## 10. 一键检查清单（照着对）

1. `http://127.0.0.1:18000/v1/models` 正常，且 `max_model_len=6000`
2. `http://127.0.0.1:8000/health` 返回 `healthy`
3. Chroma 向量数 > 0
4. LangSmith dataset 创建成功（25 条）
5. evaluate 退出码 0
6. 能拿到 `routing_accuracy/retrieval_recall/answer_correctness`
7. 若仍报上下文超限，已核对 `CHAT_MAX_*` 参数与当前服务进程
8. 报告文件已落在 `evaluation/reports/`，并记录了本轮命令与聚合指标

---

## 11. 结束后可选清理

停止 vLLM：

```powershell
wsl -d Ubuntu -- pkill -f "vllm.entrypoints.openai.api_server"
```

停止 FastAPI：

在运行 uvicorn 的终端按 `Ctrl + C`。

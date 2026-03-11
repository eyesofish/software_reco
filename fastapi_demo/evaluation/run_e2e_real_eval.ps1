$ErrorActionPreference = "Continue"

$root = "D:\Github\software_reco\fastapi_demo"
$py = "C:\Users\UYou2\.conda\envs\dech2\python.exe"
$runtime = Join-Path $root ".runtime"
$serverLog = Join-Path $runtime "fastapi_e2e_real.log"
$embeddingLog = Join-Path $runtime "embedding.log"
$reportDir = Join-Path $root "evaluation\reports"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir ("e2e_real_evaluation_report_" + $ts + ".md")

$dataset = "rag_hitl_eval_real_25"
$sampleCount = 25
$policy = "auto_confirm"
$maxConcurrency = 1
$prefix = "rag-hitl-real-" + (Get-Date -Format "yyyyMMdd-HHmmss")

# Evaluation-time router guardrail: keep fallback behavior, but avoid slow repeated connection failures.
$env:ROUTER_TIMEOUT_SECONDS = "1"
$env:ROUTER_CIRCUIT_BREAKER_SECONDS = "180"
# Evaluation-time speed guardrail: skip normalize-query LLM call for deterministic latency.
$env:QUERY_NORMALIZATION_USE_LLM = "false"

New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $reportDir | Out-Null
if (Test-Path $serverLog) {
    Remove-Item $serverLog -Force
}

function Run-InlinePython {
    param(
        [Parameter(Mandatory = $true)][string]$Code
    )
    return @"
$Code
"@ | & $py -
}

# 1) Ensure local LLM is ready.
$llmReady = $false
$llmModelsJson = ""
for ($i = 0; $i -lt 180; $i++) {
    try {
        $models = Invoke-RestMethod -Uri "http://127.0.0.1:18000/v1/models" -Method Get -TimeoutSec 5
        $llmReady = $true
        $llmModelsJson = $models | ConvertTo-Json -Depth 8
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

# 2) Export TechQA contexts.
Set-Location $root
$exportOutput = & $py -m evaluation.export_techqa_contexts_to_ingest_docs `
    --hf-dataset nvidia/TechQA-RAG-Eval `
    --hf-split test `
    --output-dir ingest_docs `
    --clean-output `
    --overwrite-existing 2>&1
$exportExit = $LASTEXITCODE

# 3) Clear Chroma collections.
$clearOutput = Run-InlinePython -Code @'
import chromadb
path = r"D:/Github/software_reco/fastapi_demo/chroma_db"
client = chromadb.PersistentClient(path=path)
cols = client.list_collections()
print("collections_before", [c.name for c in cols])
for c in cols:
    client.delete_collection(c.name)
print("deleted_count", len(cols))
col = client.get_or_create_collection("software_recommendations")
print("count_after", col.count())
'@
$clearExit = $LASTEXITCODE

# 4) Start FastAPI and wait for health.
$job = Start-Job -ScriptBlock {
    param($projectRoot, $pythonPath, $logPath)
    Set-Location $projectRoot
    & $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000 *>> $logPath
} -ArgumentList $root, $py, $serverLog

$fastapiReady = $false
$healthJson = ""
for ($i = 0; $i -lt 480; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 5
        $fastapiReady = $true
        $healthJson = $health | ConvertTo-Json -Depth 6
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

$portPid = ""
try {
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -First 1 OwningProcess
    if ($conn) {
        $portPid = [string]$conn.OwningProcess
    }
}
catch {}

# 5) Read vector count after startup ingestion.
$vectorCountOutput = Run-InlinePython -Code @'
import chromadb
path = r"D:/Github/software_reco/fastapi_demo/chroma_db"
client = chromadb.PersistentClient(path=path)
col = client.get_or_create_collection("software_recommendations")
print(col.count())
'@
$vectorCountExit = $LASTEXITCODE
$vectorCount = ($vectorCountOutput | Out-String).Trim()

$ingestSummary = ""
if (Test-Path $serverLog) {
    $summaryLine = Select-String -Path $serverLog -Pattern "startup\.ingest\.complete" | Select-Object -Last 1
    if ($summaryLine) {
        $ingestSummary = $summaryLine.Line
    }
}
if ([string]::IsNullOrWhiteSpace($ingestSummary) -and (Test-Path $embeddingLog)) {
    $summaryLine = Select-String -Path $embeddingLog -Pattern "startup\.ingest\.complete" | Select-Object -Last 1
    if ($summaryLine) {
        $ingestSummary = $summaryLine.Line
    }
}
if ([string]::IsNullOrWhiteSpace($ingestSummary) -and (Test-Path $serverLog)) {
    $summaryLine = Select-String -Path $serverLog -Pattern "startup ingest complete" | Select-Object -Last 1
    if ($summaryLine) {
        $ingestSummary = $summaryLine.Line
    }
}
if ([string]::IsNullOrWhiteSpace($ingestSummary) -and (Test-Path $embeddingLog)) {
    $summaryLine = Select-String -Path $embeddingLog -Pattern "startup ingest complete" | Select-Object -Last 1
    if ($summaryLine) {
        $ingestSummary = $summaryLine.Line
    }
}
if ([string]::IsNullOrWhiteSpace($ingestSummary)) {
    $ingestSummary = "(startup ingest summary not found)"
}

# 6) Build 25-sample LangSmith dataset.
$convertOutput = & $py -m evaluation.convert_hf_to_langsmith `
    --hf-dataset nvidia/TechQA-RAG-Eval `
    --hf-split test `
    --langsmith-dataset $dataset `
    --max-samples $sampleCount `
    --overwrite 2>&1
$convertExit = $LASTEXITCODE

# 7) Run evaluation.
$evalOutput = & $py -m evaluation.langsmith_evaluate `
    --dataset $dataset `
    --policy $policy `
    --prefix $prefix `
    --max-concurrency $maxConcurrency 2>&1
$evalExit = $LASTEXITCODE
$evalText = ($evalOutput | Out-String)

$projectId = ""
$projectMatch = [regex]::Match($evalText, "selectedSessions=([0-9a-fA-F-]{36})")
if ($projectMatch.Success) {
    $projectId = $projectMatch.Groups[1].Value
}

$experimentUrl = ""
$urlMatch = [regex]::Match($evalText, "https://smith\.langchain\.com[^\s]+")
if ($urlMatch.Success) {
    $experimentUrl = $urlMatch.Value
}
if ([string]::IsNullOrWhiteSpace($experimentUrl)) {
    $experimentUrl = "(experiment url not parsed)"
}

# 8) Pull aggregated metrics from LangSmith.
$metricsJson = ""
$metricsExit = 1
if (-not [string]::IsNullOrWhiteSpace($projectId)) {
    $metricsOutput = Run-InlinePython -Code @"
import json
from pathlib import Path
from dotenv import load_dotenv
from langsmith import Client

load_dotenv(Path(r"D:/Github/software_reco/fastapi_demo/.env"))
client = Client()
results = client.get_experiment_results(project_id="$projectId")
feedback = results.get("feedback_stats", {}) or {}
run_stats = results.get("run_stats", {}) or {}

def pick(key):
    item = feedback.get(key, {}) or {}
    return {
        "n": item.get("n"),
        "avg": item.get("avg"),
        "stdev": item.get("stdev"),
        "errors": item.get("errors"),
    }

payload = {
    "routing_accuracy": pick("routing_accuracy"),
    "retrieval_recall": pick("retrieval_recall"),
    "answer_correctness": pick("answer_correctness"),
    "run_count": run_stats.get("run_count"),
    "latency_p50": run_stats.get("latency_p50"),
    "total_tokens": run_stats.get("total_tokens"),
    "prompt_tokens": run_stats.get("prompt_tokens"),
    "completion_tokens": run_stats.get("completion_tokens"),
}
print(json.dumps(payload, ensure_ascii=False, default=str))
"@
    $metricsExit = $LASTEXITCODE
    $metricsJson = ($metricsOutput | Out-String).Trim()
}

# 9) Stop FastAPI job.
if ($job -and $job.State -eq "Running") {
    Stop-Job -Id $job.Id -ErrorAction SilentlyContinue | Out-Null
}
if ($job) {
    Remove-Job -Id $job.Id -ErrorAction SilentlyContinue
}

if ([string]::IsNullOrWhiteSpace($healthJson)) { $healthJson = "(health check failed)" }
if ([string]::IsNullOrWhiteSpace($llmModelsJson)) { $llmModelsJson = "(llm models check failed)" }
if ([string]::IsNullOrWhiteSpace($metricsJson)) { $metricsJson = "(metrics unavailable)" }

$exportText = (($exportOutput | Out-String).Trim())
$clearText = (($clearOutput | Out-String).Trim())
$convertText = (($convertOutput | Out-String).Trim())
$evalSnippet = ($evalText.Trim())
if ($evalSnippet.Length -gt 6000) {
    $evalSnippet = $evalSnippet.Substring(0, 6000) + "`n...[truncated]..."
}
$serverTail = ""
if (Test-Path $serverLog) {
    $serverTail = (Get-Content $serverLog -Tail 120) -join "`n"
}
if ([string]::IsNullOrWhiteSpace($serverTail)) { $serverTail = "(no server log captured)" }

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
$mdLines = @(
    "# FastAPI Demo End-to-End Evaluation Report (Real)"
    "- Updated: $now"
    "- Project: D:\Github\software_reco\fastapi_demo"
    "- Dataset: $dataset (samples=$sampleCount)"
    "- Policy: $policy"
    "- Max concurrency: $maxConcurrency"
    ""
    "## 1) Local LLM readiness"
    "- LLM ready: $llmReady"
    "- /v1/models response:"
    '```json'
    $llmModelsJson
    '```'
    ""
    "## 2) Data prep and vector rebuild"
    "### 2.1 Export TechQA contexts to ingest_docs"
    "- Command:"
    '```bash'
    "python -m evaluation.export_techqa_contexts_to_ingest_docs --hf-dataset nvidia/TechQA-RAG-Eval --hf-split test --output-dir ingest_docs --clean-output --overwrite-existing"
    '```'
    "- Exit code: $exportExit"
    "- Output:"
    '```text'
    $exportText
    '```'
    ""
    "### 2.2 Clear Chroma and rebuild vectors"
    "- Clear vector db exit: $clearExit"
    "- Clear output:"
    '```text'
    $clearText
    '```'
    "- FastAPI ready: $fastapiReady"
    "- Health response:"
    '```json'
    $healthJson
    '```'
    "- Port 8000 PID: $portPid"
    "- Startup ingest summary:"
    '```text'
    $ingestSummary
    '```'
    "- Current Chroma vector count (software_recommendations): $vectorCount (exit=$vectorCountExit)"
    ""
    "## 3) LangSmith dataset conversion"
    "- Command:"
    '```bash'
    "python -m evaluation.convert_hf_to_langsmith --hf-dataset nvidia/TechQA-RAG-Eval --hf-split test --langsmith-dataset $dataset --max-samples $sampleCount --overwrite"
    '```'
    "- Exit code: $convertExit"
    "- Output:"
    '```text'
    $convertText
    '```'
    ""
    "## 4) End-to-end evaluation execution"
    "- Command:"
    '```bash'
    "python -m evaluation.langsmith_evaluate --dataset $dataset --policy $policy --prefix $prefix --max-concurrency $maxConcurrency"
    '```'
    "- Exit code: $evalExit"
    "- Project/session id: $projectId"
    "- Experiment URL:"
    '```text'
    $experimentUrl
    '```'
    "- Eval output (snippet):"
    '```text'
    $evalSnippet
    '```'
    ""
    "## 5) Aggregated metrics (LangSmith)"
    "Source: Client.get_experiment_results(project_id=...)."
    '```json'
    $metricsJson
    '```'
    ""
    "## 6) FastAPI log tail"
    '```text'
    $serverTail
    '```'
)
$md = $mdLines -join "`r`n"
Set-Content -Path $reportPath -Value $md -Encoding UTF8

Write-Output "REPORT_PATH=$reportPath"
Write-Output "LLM_READY=$llmReady"
Write-Output "FASTAPI_READY=$fastapiReady"
Write-Output "EXPORT_EXIT=$exportExit"
Write-Output "CLEAR_EXIT=$clearExit"
Write-Output "CONVERT_EXIT=$convertExit"
Write-Output "EVAL_EXIT=$evalExit"
Write-Output "METRICS_EXIT=$metricsExit"
Write-Output "PROJECT_ID=$projectId"




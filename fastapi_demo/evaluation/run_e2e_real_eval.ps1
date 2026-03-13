# Usage examples:
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval.ps1
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval.ps1 -SampleCount 100
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval.ps1 -SampleCount 25 -Dataset rag_hitl_eval_custom_25
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval.ps1 -SampleCount 100 -BucketCount 3 -BucketIndex 1
param(
    [int]$SampleCount = 25,
    [string]$Dataset = "",
    [string]$HfDataset = "nvidia/TechQA-RAG-Eval",
    [string]$HfSplit = "test",
    [string]$Policy = "auto_confirm",
    [int]$MaxConcurrency = 1,
    [string]$Prefix = "",
    [int]$BucketCount = 0,
    [int]$BucketIndex = -1,
    [string]$EvalChromaPath = "",
    [string]$EvalIngestPath = "",
    [string]$EvalMemoryStoreFile = "",
    [string]$EvalSessionStateFile = ""
)

$ErrorActionPreference = "Continue"

$root = "D:\Github\software_reco\fastapi_demo"
$py = "C:\Users\UYou2\.conda\envs\dech2\python.exe"
$runtime = Join-Path $root ".runtime"
$reportDir = Join-Path $root "evaluation\reports"
$serverLog = Join-Path $runtime "fastapi_e2e_real.log"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir ("e2e_real_evaluation_report_" + $ts + ".md")

function Resolve-EvalPath {
    param(
        [AllowEmptyString()][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$DefaultPath
    )

    $effective = if ([string]::IsNullOrWhiteSpace($Candidate)) { $DefaultPath } else { $Candidate }
    if ([System.IO.Path]::IsPathRooted($effective)) {
        return $effective
    }
    return Join-Path $root $effective
}

$sampleCount = [Math]::Max(1, $SampleCount)
$dataset = if ([string]::IsNullOrWhiteSpace($Dataset)) { "rag_hitl_eval_real_$sampleCount" } else { $Dataset.Trim() }
$policy = if ([string]::IsNullOrWhiteSpace($Policy)) { "auto_confirm" } else { $Policy.Trim() }
$maxConcurrency = [Math]::Max(1, $MaxConcurrency)
$prefix = if ([string]::IsNullOrWhiteSpace($Prefix)) { "rag-hitl-real-" + (Get-Date -Format "yyyyMMdd-HHmmss") } else { $Prefix.Trim() }
$hfDataset = if ([string]::IsNullOrWhiteSpace($HfDataset)) { "nvidia/TechQA-RAG-Eval" } else { $HfDataset.Trim() }
$hfSplit = if ([string]::IsNullOrWhiteSpace($HfSplit)) { "test" } else { $HfSplit.Trim() }
$effectiveBucketCount = [Math]::Max(0, $BucketCount)
$effectiveBucketIndex = $BucketIndex
$useBuckets = $effectiveBucketCount -gt 0

if (-not $useBuckets -and $effectiveBucketIndex -ge 0) {
    throw "BucketIndex requires BucketCount > 0."
}
if ($useBuckets -and ($effectiveBucketIndex -lt 0 -or $effectiveBucketIndex -ge $effectiveBucketCount)) {
    throw "BucketIndex must be within [0, BucketCount)."
}

$evalIngest = Resolve-EvalPath -Candidate $EvalIngestPath -DefaultPath (Join-Path $runtime "ingest_docs_eval")
$evalChroma = Resolve-EvalPath -Candidate $EvalChromaPath -DefaultPath (Join-Path $runtime "chroma_db_eval")
$evalMemory = Resolve-EvalPath -Candidate $EvalMemoryStoreFile -DefaultPath (Join-Path $runtime "layered_memory_store_eval.json")
$evalSession = Resolve-EvalPath -Candidate $EvalSessionStateFile -DefaultPath (Join-Path $runtime "fastapi_session_state_eval.json")

Set-Location $root
New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $reportDir | Out-Null
New-Item -ItemType Directory -Force $evalIngest | Out-Null

foreach ($path in @($serverLog, $evalMemory, $evalSession)) {
    if (Test-Path $path) {
        Remove-Item $path -Force
    }
}

# Evaluation-time isolated runtime state.
$env:INGEST_PATH = $evalIngest
$env:CHROMA_DB_PATH = $evalChroma
$env:MEMORY_STORE_FILE = $evalMemory
$env:SESSION_STATE_FILE = $evalSession

# Evaluation-time router/latency guardrails.
$env:ROUTER_TIMEOUT_SECONDS = "1"
$env:ROUTER_CIRCUIT_BREAKER_SECONDS = "180"
$env:QUERY_NORMALIZATION_USE_LLM = "false"

function Run-InlinePython {
    param(
        [Parameter(Mandatory = $true)][string]$Code
    )
    return @"
$Code
"@ | & $py -
}

function Get-EvalRuntimeSummaryJson {
    return (Run-InlinePython -Code @'
import json
import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv


def _resolve(raw: str) -> str:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path)
    return str(path.resolve())


def _collection_name(item) -> str:
    return getattr(item, "name", None) or str(item)


def _collection_count(client, name: str) -> int:
    try:
        return client.get_collection(name).count()
    except Exception:
        return 0


child_name = "software_recommendations"
load_dotenv(Path.cwd() / ".env")
parent_name = os.environ.get("PARENT_COLLECTION_NAME", "software_recommendations_parent")
parent_enabled = os.environ.get("ENABLE_PARENT_CHILD_CHUNKING", "false").strip().lower() == "true"
chroma_path = _resolve(os.environ["CHROMA_DB_PATH"])
client = chromadb.PersistentClient(path=chroma_path)
payload = {
    "chroma_db_path": chroma_path,
    "ingest_path": _resolve(os.environ.get("INGEST_PATH", "./ingest_docs")),
    "memory_store_file": _resolve(os.environ.get("MEMORY_STORE_FILE", ".runtime/layered_memory_store.json")),
    "session_state_file": _resolve(os.environ.get("SESSION_STATE_FILE", ".runtime/fastapi_session_state.json")),
    "enable_parent_child_chunking": parent_enabled,
    "retrieval_enable_rerank": os.environ.get("RETRIEVAL_ENABLE_RERANK"),
    "parent_collection_name": parent_name,
    "child_collection_name": child_name,
    "child_collection_count": _collection_count(client, child_name),
    "parent_collection_count": _collection_count(client, parent_name) if parent_enabled else None,
    "collections": sorted(_collection_name(item) for item in client.list_collections()),
}
print(json.dumps(payload, ensure_ascii=False))
'@ | Out-String).Trim()
}

$evalRuntimeSummaryJson = Get-EvalRuntimeSummaryJson
$evalRuntimeSummary = $evalRuntimeSummaryJson | ConvertFrom-Json

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

# 2) Export TechQA contexts into eval-only ingest docs.
$exportOutput = & $py -m evaluation.export_techqa_contexts_to_ingest_docs `
    --hf-dataset $hfDataset `
    --hf-split $hfSplit `
    --output-dir $evalIngest `
    --clean-output `
    --overwrite-existing 2>&1
$exportExit = $LASTEXITCODE

# 3) Clear the eval-only Chroma path.
$clearOutput = Run-InlinePython -Code @'
import json
import os
from pathlib import Path

import chromadb


def _collection_name(item) -> str:
    return getattr(item, "name", None) or str(item)


path = Path(os.environ["CHROMA_DB_PATH"]).expanduser()
if not path.is_absolute():
    path = Path.cwd() / path
path = path.resolve()
client = chromadb.PersistentClient(path=str(path))
collections_before = sorted(_collection_name(item) for item in client.list_collections())
for name in collections_before:
    client.delete_collection(name)
collections_after = sorted(_collection_name(item) for item in client.list_collections())
print(
    json.dumps(
        {
            "chroma_db_path": str(path),
            "collections_before": collections_before,
            "deleted_count": len(collections_before),
            "collections_after": collections_after,
        },
        ensure_ascii=False,
    )
)
'@
$clearExit = $LASTEXITCODE
$clearText = ($clearOutput | Out-String).Trim()

# 4) Start FastAPI and wait for health.
$job = Start-Job -ScriptBlock {
    param(
        $projectRoot,
        $pythonPath,
        $logPath,
        $ingestPath,
        $chromaPath,
        $memoryStoreFile,
        $sessionStateFile,
        $routerTimeout,
        $routerCircuitBreaker,
        $queryNormalizationUseLlm
    )
    $env:INGEST_PATH = $ingestPath
    $env:CHROMA_DB_PATH = $chromaPath
    $env:MEMORY_STORE_FILE = $memoryStoreFile
    $env:SESSION_STATE_FILE = $sessionStateFile
    $env:ROUTER_TIMEOUT_SECONDS = $routerTimeout
    $env:ROUTER_CIRCUIT_BREAKER_SECONDS = $routerCircuitBreaker
    $env:QUERY_NORMALIZATION_USE_LLM = $queryNormalizationUseLlm
    Set-Location $projectRoot
    & $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000 *>> $logPath
} -ArgumentList $root, $py, $serverLog, $evalIngest, $evalChroma, $evalMemory, $evalSession, $env:ROUTER_TIMEOUT_SECONDS, $env:ROUTER_CIRCUIT_BREAKER_SECONDS, $env:QUERY_NORMALIZATION_USE_LLM

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

# 5) Read collection counts after startup ingestion.
$postStartupSummaryJson = Get-EvalRuntimeSummaryJson
$postStartupSummary = $postStartupSummaryJson | ConvertFrom-Json
$childCollectionCount = [string]$postStartupSummary.child_collection_count
$parentCollectionCount = if ($postStartupSummary.enable_parent_child_chunking) { [string]$postStartupSummary.parent_collection_count } else { "n/a" }

$ingestSummary = ""
if (Test-Path $serverLog) {
    $summaryLine = Select-String -Path $serverLog -Pattern "startup\.ingest\.complete|startup ingest complete" | Select-Object -Last 1
    if ($summaryLine) {
        $ingestSummary = $summaryLine.Line
    }
}
if ([string]::IsNullOrWhiteSpace($ingestSummary)) {
    $ingestSummary = "(startup ingest summary not found)"
}

# 6) Build LangSmith dataset.
$convertBucketArgs = @()
$bucketCommandSuffix = ""
if ($useBuckets) {
    $convertBucketArgs = @(
        "--bucket-count", $effectiveBucketCount,
        "--bucket-index", $effectiveBucketIndex
    )
    $bucketCommandSuffix = " --bucket-count $effectiveBucketCount --bucket-index $effectiveBucketIndex"
}
$convertOutput = & $py -m evaluation.convert_hf_to_langsmith `
    --hf-dataset $hfDataset `
    --hf-split $hfSplit `
    --langsmith-dataset $dataset `
    --max-samples $sampleCount `
    @convertBucketArgs `
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
$projectMatch = [regex]::Match($evalText, "(?m)^LANGSMITH_PROJECT_ID=(.+)$")
if ($projectMatch.Success) {
    $projectId = $projectMatch.Groups[1].Value.Trim()
}
if ([string]::IsNullOrWhiteSpace($projectId)) {
    $legacyProjectMatch = [regex]::Match($evalText, "selectedSessions=([0-9a-fA-F-]{36})")
    if ($legacyProjectMatch.Success) {
        $projectId = $legacyProjectMatch.Groups[1].Value
    }
}

$experimentName = ""
$nameMatch = [regex]::Match($evalText, "(?m)^LANGSMITH_EXPERIMENT_NAME=(.+)$")
if ($nameMatch.Success) {
    $experimentName = $nameMatch.Groups[1].Value.Trim()
}

$experimentUrl = ""
$urlMatch = [regex]::Match($evalText, "(?m)^LANGSMITH_EXPERIMENT_URL=(.+)$")
if ($urlMatch.Success) {
    $experimentUrl = $urlMatch.Groups[1].Value.Trim()
}
if ([string]::IsNullOrWhiteSpace($experimentUrl)) {
    $legacyUrlMatch = [regex]::Match($evalText, "https://smith\.langchain\.com[^\s]+")
    if ($legacyUrlMatch.Success) {
        $experimentUrl = $legacyUrlMatch.Value
    }
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
    "- Dataset: $dataset"
    "- Sample count: $sampleCount"
    "- HF dataset: $hfDataset"
    "- HF split: $hfSplit"
    "- Policy: $policy"
    "- Max concurrency: $maxConcurrency"
    "- Python: $py"
    ""
    "## 0) Effective evaluation environment"
    "- Effective SampleCount: $sampleCount"
    "- Effective Dataset: $dataset"
    "- Effective HfDataset: $hfDataset"
    "- Effective HfSplit: $hfSplit"
    "- Effective Policy: $policy"
    "- Effective MaxConcurrency: $maxConcurrency"
    "- Effective Prefix: $prefix"
    "- Effective BucketCount: $effectiveBucketCount"
    "- Effective BucketIndex: $(if ($useBuckets) { $effectiveBucketIndex } else { 'disabled' })"
    "- CHROMA_DB_PATH: $($evalRuntimeSummary.chroma_db_path)"
    "- INGEST_PATH: $($evalRuntimeSummary.ingest_path)"
    "- MEMORY_STORE_FILE: $($evalRuntimeSummary.memory_store_file)"
    "- SESSION_STATE_FILE: $($evalRuntimeSummary.session_state_file)"
    "- ENABLE_PARENT_CHILD_CHUNKING: $($evalRuntimeSummary.enable_parent_child_chunking)"
    "- RETRIEVAL_ENABLE_RERANK: $($evalRuntimeSummary.retrieval_enable_rerank)"
    "- Parent collection name: $($evalRuntimeSummary.parent_collection_name)"
    ""
    "## 1) Local LLM readiness"
    "- LLM ready: $llmReady"
    "- /v1/models response:"
    '```json'
    $llmModelsJson
    '```'
    ""
    "## 2) Data prep and vector rebuild"
    "### 2.1 Export TechQA contexts to eval ingest docs"
    "- Command:"
    '```bash'
    "$py -m evaluation.export_techqa_contexts_to_ingest_docs --hf-dataset $hfDataset --hf-split $hfSplit --output-dir `"$evalIngest`" --clean-output --overwrite-existing"
    '```'
    "- Exit code: $exportExit"
    "- Output:"
    '```text'
    $exportText
    '```'
    ""
    "### 2.2 Clear eval Chroma and rebuild vectors"
    "- Clear vector db exit: $clearExit"
    "- Clear output:"
    '```json'
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
    "- Child collection count ($($postStartupSummary.child_collection_name)): $childCollectionCount"
    "- Parent collection count ($($postStartupSummary.parent_collection_name)): $parentCollectionCount"
    "- Collection summary after startup:"
    '```json'
    $postStartupSummaryJson
    '```'
    ""
    "## 3) LangSmith dataset conversion"
    "- Command:"
    '```bash'
    "$py -m evaluation.convert_hf_to_langsmith --hf-dataset $hfDataset --hf-split $hfSplit --langsmith-dataset $dataset --max-samples $sampleCount$bucketCommandSuffix --overwrite"
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
    "$py -m evaluation.langsmith_evaluate --dataset $dataset --policy $policy --prefix $prefix --max-concurrency $maxConcurrency"
    '```'
    "- Exit code: $evalExit"
    "- Experiment name: $experimentName"
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
Write-Output "EFFECTIVE_CHROMA_DB_PATH=$($evalRuntimeSummary.chroma_db_path)"
Write-Output "EFFECTIVE_INGEST_PATH=$($evalRuntimeSummary.ingest_path)"
Write-Output "EFFECTIVE_BUCKET_COUNT=$effectiveBucketCount"
Write-Output "EFFECTIVE_BUCKET_INDEX=$(if ($useBuckets) { $effectiveBucketIndex } else { 'disabled' })"
Write-Output "FASTAPI_READY=$fastapiReady"
Write-Output "EXPORT_EXIT=$exportExit"
Write-Output "CLEAR_EXIT=$clearExit"
Write-Output "CONVERT_EXIT=$convertExit"
Write-Output "EVAL_EXIT=$evalExit"
Write-Output "METRICS_EXIT=$metricsExit"
Write-Output "PROJECT_ID=$projectId"

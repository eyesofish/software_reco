$ErrorActionPreference = "Continue"

$root = "D:\Github\software_reco\fastapi_demo"
$py = "C:\Users\UYou2\.conda\envs\dech2\python.exe"
$runtime = Join-Path $root ".runtime"
$emptyIngest = Join-Path $runtime "ingest_docs_eval_smoke_empty"
$serverLog = Join-Path $runtime "fastapi_e2e_smoke.log"
$reportDir = Join-Path $root "evaluation\reports"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir ("e2e_smoke_evaluation_report_" + $ts + ".md")
$dataset = "rag_hitl_eval_smoke_1"
$prefix = "rag-hitl-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss")

$evalChroma = Join-Path $runtime "chroma_db_eval_smoke"
$evalMemory = Join-Path $runtime "layered_memory_store_eval_smoke.json"
$evalSession = Join-Path $runtime "fastapi_session_state_eval_smoke.json"

Set-Location $root
New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $emptyIngest | Out-Null
New-Item -ItemType Directory -Force $reportDir | Out-Null

foreach ($path in @($serverLog, $evalMemory, $evalSession)) {
    if (Test-Path $path) {
        Remove-Item $path -Force
    }
}

$env:INGEST_PATH = $emptyIngest
$env:CHROMA_DB_PATH = $evalChroma
$env:MEMORY_STORE_FILE = $evalMemory
$env:SESSION_STATE_FILE = $evalSession
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

$job = Start-Job -ScriptBlock {
    param(
        $projectRoot,
        $pythonPath,
        $ingestPath,
        $logPath,
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
} -ArgumentList $root, $py, $emptyIngest, $serverLog, $evalChroma, $evalMemory, $evalSession, $env:ROUTER_TIMEOUT_SECONDS, $env:ROUTER_CIRCUIT_BREAKER_SECONDS, $env:QUERY_NORMALIZATION_USE_LLM

$fastapiReady = $false
$healthResp = ""
for ($i = 0; $i -lt 90; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 3
        $fastapiReady = $true
        $healthResp = $health | ConvertTo-Json -Depth 6
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

$convertOutput = & $py -m evaluation.convert_hf_to_langsmith `
    --hf-dataset nvidia/TechQA-RAG-Eval `
    --hf-split test `
    --langsmith-dataset $dataset `
    --max-samples 1 `
    --overwrite 2>&1
$convertExit = $LASTEXITCODE

$evalOutput = & $py -m evaluation.langsmith_evaluate `
    --dataset $dataset `
    --policy auto_confirm `
    --prefix $prefix `
    --max-concurrency 1 2>&1
$evalExit = $LASTEXITCODE
$evalText = ($evalOutput | Out-String).Trim()

$projectId = ""
$projectMatch = [regex]::Match($evalText, "(?m)^LANGSMITH_PROJECT_ID=(.+)$")
if ($projectMatch.Success) {
    $projectId = $projectMatch.Groups[1].Value.Trim()
}

$experimentUrl = ""
$urlMatch = [regex]::Match($evalText, "(?m)^LANGSMITH_EXPERIMENT_URL=(.+)$")
if ($urlMatch.Success) {
    $experimentUrl = $urlMatch.Groups[1].Value.Trim()
}

if ($job -and $job.State -eq "Running") {
    Stop-Job -Id $job.Id -ErrorAction SilentlyContinue | Out-Null
}
if ($job) {
    Remove-Job -Id $job.Id -ErrorAction SilentlyContinue
}

$serverTail = ""
if (Test-Path $serverLog) {
    $serverTail = (Get-Content $serverLog -Tail 120) -join "`n"
}

$convertText = ($convertOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($convertText)) {
    $convertText = "(no output)"
}
if ([string]::IsNullOrWhiteSpace($evalText)) {
    $evalText = "(no output)"
}
if ([string]::IsNullOrWhiteSpace($serverTail)) {
    $serverTail = "(no server log captured)"
}
if ([string]::IsNullOrWhiteSpace($healthResp)) {
    $healthResp = "(health check failed)"
}
if ([string]::IsNullOrWhiteSpace($experimentUrl)) {
    $experimentUrl = "(experiment url not parsed)"
}

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
$mdLines = @(
    "# FastAPI Demo End-to-End Evaluation Report (Smoke)"
    "- Time: $now"
    "- Project: D:\Github\software_reco\fastapi_demo"
    "- Python: $py"
    ""
    "## 0) Effective evaluation environment"
    "- CHROMA_DB_PATH: $($evalRuntimeSummary.chroma_db_path)"
    "- INGEST_PATH: $($evalRuntimeSummary.ingest_path)"
    "- MEMORY_STORE_FILE: $($evalRuntimeSummary.memory_store_file)"
    "- SESSION_STATE_FILE: $($evalRuntimeSummary.session_state_file)"
    "- ENABLE_PARENT_CHILD_CHUNKING: $($evalRuntimeSummary.enable_parent_child_chunking)"
    "- RETRIEVAL_ENABLE_RERANK: $($evalRuntimeSummary.retrieval_enable_rerank)"
    "- Parent collection name: $($evalRuntimeSummary.parent_collection_name)"
    ""
    "## 1) FastAPI startup"
    "- Startup method: uvicorn app.main:app --host 0.0.0.0 --port 8000 (job mode)"
    "- Job Id: $($job.Id)"
    "- Port 8000 Owning PID: $portPid"
    "- Health ready: $fastapiReady"
    "- Health response:"
    '```json'
    $healthResp
    '```'
    "- Clear eval Chroma exit: $clearExit"
    "- Clear output:"
    '```json'
    $clearText
    '```'
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
    "## 2) Dataset preparation (1 sample)"
    "- Dataset name: $dataset"
    "- Command:"
    '```bash'
    "$py -m evaluation.convert_hf_to_langsmith --hf-dataset nvidia/TechQA-RAG-Eval --hf-split test --langsmith-dataset $dataset --max-samples 1 --overwrite"
    '```'
    "- Exit code: $convertExit"
    "- Output:"
    '```text'
    $convertText
    '```'
    ""
    "## 3) End-to-end evaluation run"
    "- Command:"
    '```bash'
    "$py -m evaluation.langsmith_evaluate --dataset $dataset --policy auto_confirm --prefix $prefix --max-concurrency 1"
    '```'
    "- Exit code: $evalExit"
    "- Project/session id: $projectId"
    "- Experiment URL:"
    '```text'
    $experimentUrl
    '```'
    "- Output:"
    '```text'
    $evalText
    '```'
    ""
    "## 4) FastAPI log tail"
    '```text'
    $serverTail
    '```'
)
$md = $mdLines -join "`r`n"
Set-Content -Path $reportPath -Value $md -Encoding UTF8

Write-Output "REPORT_PATH=$reportPath"
Write-Output "EFFECTIVE_CHROMA_DB_PATH=$($evalRuntimeSummary.chroma_db_path)"
Write-Output "FASTAPI_READY=$fastapiReady"
Write-Output "CLEAR_EXIT=$clearExit"
Write-Output "CONVERT_EXIT=$convertExit"
Write-Output "EVAL_EXIT=$evalExit"
Write-Output "PROJECT_ID=$projectId"

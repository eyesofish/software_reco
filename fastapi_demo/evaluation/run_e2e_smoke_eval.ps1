$ErrorActionPreference = "Continue"

$root = "D:\Github\software_reco\fastapi_demo"
$py = "C:\Users\UYou2\.conda\envs\dech2\python.exe"
$runtime = Join-Path $root ".runtime"
$emptyIngest = Join-Path $runtime "empty_ingest"
$serverLog = Join-Path $runtime "fastapi_e2e.log"
$reportDir = Join-Path $root "evaluation\reports"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir ("e2e_smoke_evaluation_report_" + $ts + ".md")
$dataset = "rag_hitl_eval_smoke_1"
$prefix = "rag-hitl-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss")

New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $emptyIngest | Out-Null
New-Item -ItemType Directory -Force $reportDir | Out-Null
if (Test-Path $serverLog) {
    Remove-Item $serverLog -Force
}

$job = Start-Job -ScriptBlock {
    param($projectRoot, $pythonPath, $ingestPath, $logPath)
    $env:INGEST_PATH = $ingestPath
    Set-Location $projectRoot
    & $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000 *>> $logPath
} -ArgumentList $root, $py, $emptyIngest, $serverLog

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

Set-Location $root
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

$serverTail = ""
if (Test-Path $serverLog) {
    $serverTail = (Get-Content $serverLog -Tail 120) -join "`n"
}

if ($job -and $job.State -eq "Running") {
    Stop-Job -Id $job.Id -ErrorAction SilentlyContinue | Out-Null
}
if ($job) {
    Remove-Job -Id $job.Id -ErrorAction SilentlyContinue
}

$convertText = ($convertOutput | Out-String).Trim()
$evalText = ($evalOutput | Out-String).Trim()
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

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
$md = @"
# FastAPI Demo End-to-End Evaluation Report

- Time: $now
- Project: `D:\Github\software_reco\fastapi_demo`
- Python: `$py`

## 1) FastAPI Startup

- Startup method: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (job mode)
- Job Id: $($job.Id)
- Port 8000 Owning PID: $portPid
- Health ready: $fastapiReady
- Health response:

```json
$healthResp
```

## 2) Dataset Preparation (1 sample)

- Dataset name: `$dataset`
- Command:

```bash
python -m evaluation.convert_hf_to_langsmith --hf-dataset nvidia/TechQA-RAG-Eval --hf-split test --langsmith-dataset $dataset --max-samples 1 --overwrite
```

- Exit code: $convertExit
- Output:

```text
$convertText
```

## 3) End-to-End Evaluation Run

- Command:

```bash
python -m evaluation.langsmith_evaluate --dataset $dataset --policy auto_confirm --prefix $prefix --max-concurrency 1
```

- Exit code: $evalExit
- Output:

```text
$evalText
```

## 4) FastAPI Log Tail

```text
$serverTail
```

## 5) Conclusion

- FastAPI started and health check returned: `$fastapiReady`.
- Smoke dataset conversion executed with exit code `$convertExit`.
- End-to-end evaluation command executed with exit code `$evalExit`.
"@

Set-Content -Path $reportPath -Value $md -Encoding UTF8

Write-Output "REPORT_PATH=$reportPath"
Write-Output "FASTAPI_READY=$fastapiReady"
Write-Output "CONVERT_EXIT=$convertExit"
Write-Output "EVAL_EXIT=$evalExit"

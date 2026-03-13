# Usage examples:
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval_3x100.ps1
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_e2e_real_eval_3x100.ps1 -SampleCount 100
param(
    [int]$SampleCount = 100,
    [string]$HfDataset = "nvidia/TechQA-RAG-Eval",
    [string]$HfSplit = "test",
    [string]$Policy = "auto_confirm",
    [int]$MaxConcurrency = 1
)

$ErrorActionPreference = "Stop"

$root = "D:\Github\software_reco\fastapi_demo"
$runtime = Join-Path $root ".runtime"
$reportDir = Join-Path $root "evaluation\reports"
$realEvalScript = Join-Path $root "evaluation\run_e2e_real_eval.ps1"
$powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$bucketCount = 3
$effectiveSampleCount = [Math]::Max(1, $SampleCount)
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$summaryPath = Join-Path $reportDir ("e2e_real_eval_3x100_summary_" + $ts + ".md")

New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $reportDir | Out-Null

function Get-MarkerValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Key
    )

    $pattern = "(?m)^" + [regex]::Escape($Key) + "=(.+)$"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}

function Get-ReportMetrics {
    param(
        [Parameter(Mandatory = $true)][string]$ReportPath
    )

    $content = Get-Content -Path $ReportPath -Raw
    $options = [System.Text.RegularExpressions.RegexOptions]::Singleline
    $match = [regex]::Match(
        $content,
        '## 5\) Aggregated metrics \(LangSmith\).*?```json\s*(\{.*?\})\s*```',
        $options
    )
    if (-not $match.Success) {
        throw "Could not parse metrics JSON from report: $ReportPath"
    }
    return $match.Groups[1].Value | ConvertFrom-Json
}

$results = @()

for ($bucketIndex = 0; $bucketIndex -lt $bucketCount; $bucketIndex++) {
    $dataset = "rag_hitl_eval_real_b${bucketIndex}_${effectiveSampleCount}"
    $prefix = "rag-hitl-real-b${bucketIndex}-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $evalChromaPath = Join-Path $runtime ("chroma_db_eval_b" + $bucketIndex)
    $evalIngestPath = Join-Path $runtime ("ingest_docs_eval_b" + $bucketIndex)
    $evalMemoryStoreFile = Join-Path $runtime ("layered_memory_store_eval_b" + $bucketIndex + ".json")
    $evalSessionStateFile = Join-Path $runtime ("fastapi_session_state_eval_b" + $bucketIndex + ".json")

    $invokeArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $realEvalScript,
        "-SampleCount", $effectiveSampleCount,
        "-Dataset", $dataset,
        "-HfDataset", $HfDataset,
        "-HfSplit", $HfSplit,
        "-Policy", $Policy,
        "-MaxConcurrency", $MaxConcurrency,
        "-Prefix", $prefix,
        "-BucketCount", $bucketCount,
        "-BucketIndex", $bucketIndex,
        "-EvalChromaPath", $evalChromaPath,
        "-EvalIngestPath", $evalIngestPath,
        "-EvalMemoryStoreFile", $evalMemoryStoreFile,
        "-EvalSessionStateFile", $evalSessionStateFile
    )

    $runOutput = & $powershellExe @invokeArgs 2>&1
    $runExit = $LASTEXITCODE
    $runText = (($runOutput | ForEach-Object { $_.ToString() }) -join "`n").Trim()

    if ($runExit -ne 0) {
        throw "Bucket $bucketIndex run failed with exit code $($runExit).`n$runText"
    }

    $reportPath = Get-MarkerValue -Text $runText -Key "REPORT_PATH"
    $projectId = Get-MarkerValue -Text $runText -Key "PROJECT_ID"
    $fastapiReady = Get-MarkerValue -Text $runText -Key "FASTAPI_READY"
    $exportExit = Get-MarkerValue -Text $runText -Key "EXPORT_EXIT"
    $clearExit = Get-MarkerValue -Text $runText -Key "CLEAR_EXIT"
    $convertExit = Get-MarkerValue -Text $runText -Key "CONVERT_EXIT"
    $evalExit = Get-MarkerValue -Text $runText -Key "EVAL_EXIT"
    $metricsExit = Get-MarkerValue -Text $runText -Key "METRICS_EXIT"

    if (
        [string]::IsNullOrWhiteSpace($reportPath) -or
        $fastapiReady -ne "True" -or
        $exportExit -ne "0" -or
        $clearExit -ne "0" -or
        $convertExit -ne "0" -or
        $evalExit -ne "0" -or
        $metricsExit -ne "0"
    ) {
        throw "Bucket $bucketIndex run did not complete cleanly.`n$runText"
    }

    if (-not (Test-Path $reportPath)) {
        throw "Bucket $bucketIndex report path does not exist: $reportPath"
    }

    $metrics = Get-ReportMetrics -ReportPath $reportPath
    $results += [pscustomobject]@{
        BucketIndex = $bucketIndex
        Dataset = $dataset
        Prefix = $prefix
        ReportPath = $reportPath
        ProjectId = $projectId
        RoutingAccuracy = $metrics.routing_accuracy.avg
        RetrievalRecall = $metrics.retrieval_recall.avg
        AnswerCorrectness = $metrics.answer_correctness.avg
        LatencyP50 = [string]$metrics.latency_p50
    }
}

$summaryLines = @(
    "# FastAPI Demo End-to-End Evaluation Summary (3 Buckets)"
    "- Updated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")"
    "- Project: D:\Github\software_reco\fastapi_demo"
    "- Sample count per bucket: $effectiveSampleCount"
    "- Bucket count: $bucketCount"
    "- HF dataset: $HfDataset"
    "- HF split: $HfSplit"
    "- Policy: $Policy"
    "- Max concurrency: $MaxConcurrency"
    ""
    "## Runs"
)

foreach ($item in $results) {
    $summaryLines += "- Bucket $($item.BucketIndex): dataset=$($item.Dataset) project_id=$($item.ProjectId)"
    $summaryLines += "- Report: $($item.ReportPath)"
    $summaryLines += "- routing_accuracy=$($item.RoutingAccuracy) retrieval_recall=$($item.RetrievalRecall) answer_correctness=$($item.AnswerCorrectness) latency_p50=$($item.LatencyP50)"
    $summaryLines += ""
}

$summaryContent = $summaryLines -join "`r`n"
Set-Content -Path $summaryPath -Value $summaryContent -Encoding UTF8

foreach ($item in $results) {
    Write-Output ("REPORT_{0}={1}" -f $item.BucketIndex, $item.ReportPath)
    Write-Output ("PROJECT_{0}={1}" -f $item.BucketIndex, $item.ProjectId)
}
Write-Output "SUMMARY_PATH=$summaryPath"

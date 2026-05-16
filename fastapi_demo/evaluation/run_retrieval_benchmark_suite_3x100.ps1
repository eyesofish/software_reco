# Usage examples:
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_retrieval_benchmark_suite_3x100.ps1
# powershell -NoProfile -ExecutionPolicy Bypass -File D:\Github\software_reco\fastapi_demo\evaluation\run_retrieval_benchmark_suite_3x100.ps1 -SampleCount 5
param(
    [int]$SampleCount = 100,
    [string]$HfDataset = "nvidia/TechQA-RAG-Eval",
    [string]$HfSplit = "test"
)

$ErrorActionPreference = "Stop"

$root = "D:\Github\software_reco\fastapi_demo"
$py = "C:\Users\UYou2\.conda\envs\dech2\python.exe"
$runtime = Join-Path $root ".runtime\retrieval_eval"
$reportRoot = Join-Path $root "evaluation\reports"
$effectiveSampleCount = [Math]::Max(1, $SampleCount)
$bucketCount = 3
$topK = 10
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$suiteDir = Join-Path $reportRoot ("retrieval_benchmark_suite_" + $ts)
$rawDir = Join-Path $suiteDir "raw"
$baselineReportPath = Join-Path $reportRoot ("retrieval_baseline_summary_" + $ts + ".md")
$ablationReportPath = Join-Path $reportRoot ("retrieval_ablation_summary_" + $ts + ".md")
$summaryJsonPath = Join-Path $suiteDir "retrieval_benchmark_suite_summary.json"

$ingestPath = Join-Path $runtime "ingest_docs"
$indexRoot = Join-Path $runtime "indices"
$parentChildChroma = Join-Path $indexRoot "parent_child\chroma_db"
$childOnlyChroma = Join-Path $indexRoot "child_only\chroma_db"
$parentBuildJson = Join-Path $rawDir "index_parent_child.json"
$childBuildJson = Join-Path $rawDir "index_child_only.json"

Set-Location $root
New-Item -ItemType Directory -Force $runtime | Out-Null
New-Item -ItemType Directory -Force $reportRoot | Out-Null
New-Item -ItemType Directory -Force $suiteDir | Out-Null
New-Item -ItemType Directory -Force $rawDir | Out-Null
New-Item -ItemType Directory -Force $ingestPath | Out-Null

$methods = @(
    [pscustomobject]@{ Name = "bm25_only"; Label = "BM25-only"; Group = "baseline"; IndexMode = "parent_child" }
    [pscustomobject]@{ Name = "vector_only"; Label = "Vector-only"; Group = "baseline"; IndexMode = "parent_child" }
    [pscustomobject]@{ Name = "hybrid_no_rerank"; Label = "BM25 + Vector"; Group = "baseline"; IndexMode = "parent_child" }
    [pscustomobject]@{ Name = "hybrid_rerank"; Label = "BM25 + Vector + Rerank"; Group = "baseline"; IndexMode = "parent_child" }
    [pscustomobject]@{ Name = "vector_child_only"; Label = "Vector-only + child-only + no-rerank"; Group = "ablation"; IndexMode = "child_only" }
    [pscustomobject]@{ Name = "vector_parent_child"; Label = "Vector-only + parent-child + no-rerank"; Group = "ablation"; IndexMode = "parent_child" }
)

function Invoke-AndCapture {
    param(
        [Parameter(Mandatory = $true)][string[]]$CommandArgs,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    $output = & $py @CommandArgs 2>&1
    $exitCode = $LASTEXITCODE
    $text = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "$FailureMessage`n$text"
    }
    return $text
}

function Get-Mean {
    param([Parameter(Mandatory = $true)][double[]]$Values)
    if (-not $Values -or $Values.Count -eq 0) {
        return $null
    }
    return ($Values | Measure-Object -Average).Average
}

function Format-Metric {
    param([Parameter(Mandatory = $false)]$Value)
    if ($null -eq $Value) {
        return "n/a"
    }
    return ("{0:N4}" -f [double]$Value)
}

function New-SummaryRows {
    param(
        [Parameter(Mandatory = $true)]$Results,
        [Parameter(Mandatory = $true)]$Methods
    )

    $rows = @()
    foreach ($method in $Methods) {
        $methodRuns = @($Results | Where-Object { $_.Method -eq $method.Name } | Sort-Object BucketIndex)
        $rows += [pscustomobject]@{
            Method = $method.Name
            Label = $method.Label
            Group = $method.Group
            MeanRecallAt5 = Get-Mean ($methodRuns | ForEach-Object { [double]$_.Aggregate.recall_at_5 })
            MeanRecallAt10 = Get-Mean ($methodRuns | ForEach-Object { [double]$_.Aggregate.recall_at_10 })
            MeanHitAt5 = Get-Mean ($methodRuns | ForEach-Object { [double]$_.Aggregate.hit_at_5 })
            MeanHitAt10 = Get-Mean ($methodRuns | ForEach-Object { [double]$_.Aggregate.hit_at_10 })
            MeanNdcgAt10 = Get-Mean ($methodRuns | ForEach-Object { [double]$_.Aggregate.ndcg_at_10 })
            Runs = $methodRuns
        }
    }
    return $rows
}

function New-MarkdownTableLines {
    param([Parameter(Mandatory = $true)]$Rows)

    $lines = @(
        "| Method | Recall@5 | Recall@10 | Hit@5 | Hit@10 | NDCG@10 |"
        "| --- | ---: | ---: | ---: | ---: | ---: |"
    )
    foreach ($row in $Rows) {
        $lines += "| $($row.Label) | $(Format-Metric $row.MeanRecallAt5) | $(Format-Metric $row.MeanRecallAt10) | $(Format-Metric $row.MeanHitAt5) | $(Format-Metric $row.MeanHitAt10) | $(Format-Metric $row.MeanNdcgAt10) |"
    }
    return $lines
}

function New-PerBucketLines {
    param([Parameter(Mandatory = $true)]$Rows)

    $lines = @(
        "| Method | Bucket | Recall@5 | Recall@10 | Hit@5 | Hit@10 | NDCG@10 | Eligible |"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    foreach ($row in $Rows) {
        foreach ($run in $row.Runs) {
            $lines += "| $($row.Label) | $($run.BucketIndex) | $(Format-Metric $run.Aggregate.recall_at_5) | $(Format-Metric $run.Aggregate.recall_at_10) | $(Format-Metric $run.Aggregate.hit_at_5) | $(Format-Metric $run.Aggregate.hit_at_10) | $(Format-Metric $run.Aggregate.ndcg_at_10) | $($run.Counts.eligible_examples) |"
        }
    }
    return $lines
}

# 1) Export the full TechQA corpus once for retrieval indexing.
$null = Invoke-AndCapture -CommandArgs @(
    "-m", "evaluation.export_techqa_contexts_to_ingest_docs",
    "--hf-dataset", $HfDataset,
    "--hf-split", $HfSplit,
    "--output-dir", $ingestPath,
    "--clean-output",
    "--overwrite-existing"
) -FailureMessage "TechQA export failed."

# 2) Build the shared parent-child index and the child-only ablation index.
$null = Invoke-AndCapture -CommandArgs @(
    "-m", "evaluation.build_retrieval_eval_index",
    "--chroma-path", $parentChildChroma,
    "--ingest-path", $ingestPath,
    "--enable-parent-child", "true",
    "--output-json", $parentBuildJson
) -FailureMessage "Parent-child retrieval index build failed."

$null = Invoke-AndCapture -CommandArgs @(
    "-m", "evaluation.build_retrieval_eval_index",
    "--chroma-path", $childOnlyChroma,
    "--ingest-path", $ingestPath,
    "--enable-parent-child", "false",
    "--output-json", $childBuildJson
) -FailureMessage "Child-only retrieval index build failed."

# 3) Ensure the three deterministic bucket datasets exist with the target sample count.
$datasets = @()
for ($bucketIndex = 0; $bucketIndex -lt $bucketCount; $bucketIndex++) {
    $datasetName = "rag_retrieval_eval_b${bucketIndex}_${effectiveSampleCount}"
    $null = Invoke-AndCapture -CommandArgs @(
        "-m", "evaluation.convert_hf_to_langsmith",
        "--hf-dataset", $HfDataset,
        "--hf-split", $HfSplit,
        "--langsmith-dataset", $datasetName,
        "--max-samples", $effectiveSampleCount,
        "--bucket-count", $bucketCount,
        "--bucket-index", $bucketIndex,
        "--overwrite"
    ) -FailureMessage "Dataset conversion failed for bucket $bucketIndex."

    $datasets += [pscustomobject]@{
        BucketIndex = $bucketIndex
        Dataset = $datasetName
    }
}

# 4) Run the retrieval benchmark matrix.
$results = @()
$memoryRoot = Join-Path $runtime "memory"
$sessionRoot = Join-Path $runtime "session"
New-Item -ItemType Directory -Force $memoryRoot | Out-Null
New-Item -ItemType Directory -Force $sessionRoot | Out-Null

foreach ($method in $methods) {
    foreach ($datasetRow in $datasets) {
        $bucketIndex = [int]$datasetRow.BucketIndex
        $datasetName = [string]$datasetRow.Dataset
        $resultJson = Join-Path $rawDir ("{0}__b{1}.json" -f $method.Name, $bucketIndex)
        $resultCsv = Join-Path $rawDir ("{0}__b{1}.csv" -f $method.Name, $bucketIndex)

        $selectedChroma = if ($method.IndexMode -eq "child_only") { $childOnlyChroma } else { $parentChildChroma }
        $env:CHROMA_DB_PATH = $selectedChroma
        $env:INGEST_PATH = $ingestPath
        $env:ENABLE_PARENT_CHILD_CHUNKING = if ($method.IndexMode -eq "child_only") { "false" } else { "true" }
        $env:MEMORY_STORE_FILE = Join-Path $memoryRoot ("{0}.json" -f $method.Name)
        $env:SESSION_STATE_FILE = Join-Path $sessionRoot ("{0}.json" -f $method.Name)

        $null = Invoke-AndCapture -CommandArgs @(
            "-m", "evaluation.retrieval_benchmark",
            "--dataset", $datasetName,
            "--method", $method.Name,
            "--top-k", $topK,
            "--output-json", $resultJson,
            "--output-csv", $resultCsv
        ) -FailureMessage ("Retrieval benchmark failed for method={0} bucket={1}." -f $method.Name, $bucketIndex)

        if (-not (Test-Path $resultJson)) {
            throw "Missing retrieval benchmark JSON result: $resultJson"
        }

        $payload = Get-Content -Path $resultJson -Raw | ConvertFrom-Json
        $results += [pscustomobject]@{
            Method = $method.Name
            Label = $method.Label
            Group = $method.Group
            IndexMode = $method.IndexMode
            BucketIndex = $bucketIndex
            Dataset = $datasetName
            ResultPath = $resultJson
            Aggregate = $payload.aggregate
            Counts = $payload.counts
            Runtime = $payload.runtime
        }
    }
}

$baselineRows = New-SummaryRows -Results $results -Methods ($methods | Where-Object { $_.Group -eq "baseline" })
$ablationRows = New-SummaryRows -Results $results -Methods ($methods | Where-Object { $_.Group -eq "ablation" })

$parentBuildSummary = Get-Content -Path $parentBuildJson -Raw | ConvertFrom-Json
$childBuildSummary = Get-Content -Path $childBuildJson -Raw | ConvertFrom-Json

$commonHeader = @(
    "- Updated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")"
    "- Project: D:\Github\software_reco\fastapi_demo"
    "- HF dataset: $HfDataset"
    "- HF split: $HfSplit"
    "- sample_count=$effectiveSampleCount"
    "- bucket_count=$bucketCount"
    "- top_k=$topK"
    "- Fixed conditions: web off, memory off"
    "- Raw result directory: $rawDir"
)

$baselineLines = @(
    "# Retrieval Baseline Summary"
) + $commonHeader + @(
    "- Shared index mode for this table: parent-child enabled"
    ""
    "## Mean Metrics Across 3 Buckets"
) + (New-MarkdownTableLines -Rows $baselineRows) + @(
    ""
    "## Per-Bucket Metrics"
) + (New-PerBucketLines -Rows $baselineRows) + @(
    ""
    "## Shared Index Summary"
    "- Parent-child index path: $($parentBuildSummary.chroma_db_path)"
    "- Child collection count: $($parentBuildSummary.child_collection_count)"
    "- Parent collection count: $($parentBuildSummary.parent_collection_count)"
)
$baselineContent = $baselineLines -join "`r`n"
Set-Content -Path $baselineReportPath -Value $baselineContent -Encoding UTF8

$ablationLines = @(
    "# Retrieval Ablation Summary"
) + $commonHeader + @(
    "- Ablation difference: ENABLE_PARENT_CHILD_CHUNKING=false vs true"
    ""
    "## Mean Metrics Across 3 Buckets"
) + (New-MarkdownTableLines -Rows $ablationRows) + @(
    ""
    "## Per-Bucket Metrics"
) + (New-PerBucketLines -Rows $ablationRows) + @(
    ""
    "## Index Summaries"
    "- Child-only index path: $($childBuildSummary.chroma_db_path)"
    "- Child-only child collection count: $($childBuildSummary.child_collection_count)"
    "- Child-only parent collection count: $($childBuildSummary.parent_collection_count)"
    "- Parent-child index path: $($parentBuildSummary.chroma_db_path)"
    "- Parent-child child collection count: $($parentBuildSummary.child_collection_count)"
    "- Parent-child parent collection count: $($parentBuildSummary.parent_collection_count)"
)
$ablationContent = $ablationLines -join "`r`n"
Set-Content -Path $ablationReportPath -Value $ablationContent -Encoding UTF8

$summaryPayload = [pscustomobject]@{
    baseline_report = $baselineReportPath
    ablation_report = $ablationReportPath
    raw_result_dir = $rawDir
    baseline_rows = $baselineRows
    ablation_rows = $ablationRows
    parent_child_index = $parentBuildSummary
    child_only_index = $childBuildSummary
    results = $results
}
$summaryPayload | ConvertTo-Json -Depth 50 | Set-Content -Path $summaryJsonPath -Encoding UTF8

Write-Output "BASELINE_REPORT=$baselineReportPath"
Write-Output "ABLATION_REPORT=$ablationReportPath"
Write-Output "RAW_RESULT_DIR=$rawDir"
Write-Output "SUMMARY_JSON=$summaryJsonPath"

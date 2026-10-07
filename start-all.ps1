[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipElasticsearch,
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

function Require-Path {
    param(
        [string]$PathValue,
        [string]$Description
    )
    if (-not (Test-Path $PathValue)) {
        throw "Missing ${Description}: $PathValue"
    }
}

function Require-Command {
    param(
        [string]$Name,
        [string]$Hint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing command '$Name'. $Hint"
    }
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FastApiDir = Join-Path $RepoRoot "fastapi_demo"
$SpringDir = Join-Path $RepoRoot "ollama_springboot\demo"
$FrontendDir = Join-Path $RepoRoot "ollama_springboot\ollama-gui-reactjs"

Require-Path -PathValue $FastApiDir -Description "FastAPI directory"
Require-Path -PathValue $SpringDir -Description "SpringBoot directory"
Require-Path -PathValue $FrontendDir -Description "Frontend directory"

Require-Command -Name "python" -Hint "Install Python and ensure it is on PATH."
Require-Path -PathValue (Join-Path $SpringDir "mvnw.cmd") -Description "SpringBoot mvnw.cmd"

$frontendPm = $null
if (-not $SkipFrontend) {
    if (Get-Command "yarn" -ErrorAction SilentlyContinue) {
        $frontendPm = "yarn"
    } elseif (Get-Command "npm" -ErrorAction SilentlyContinue) {
        $frontendPm = "npm"
    } else {
        throw "Missing frontend package manager. Install 'yarn' or 'npm', or use -SkipFrontend."
    }
}

if ($InstallDeps) {
    Write-Host "[Install] FastAPI dependencies..." -ForegroundColor Cyan
    Push-Location $FastApiDir
    python -m pip install -r requirements.txt
    Pop-Location

    if (-not $SkipFrontend) {
        Write-Host "[Install] Frontend dependencies..." -ForegroundColor Cyan
        Push-Location $FrontendDir
        if ($frontendPm -eq "yarn") {
            yarn
        } else {
            npm install
        }
        Pop-Location
    }
}

$started = @()

if (-not $SkipElasticsearch) {
    Write-Host "[Start] Elasticsearch -> http://127.0.0.1:9200" -ForegroundColor Green
    try {
        Require-Command -Name "docker" -Hint "Install Docker Desktop or use -SkipElasticsearch."
        $esLog = Join-Path $RepoRoot ".runtime"
        New-Item -Path $esLog -ItemType Directory -Force | Out-Null
        # Use a bounded background process: a first image pull can take minutes.
        $esProcess = Start-Process -FilePath "docker" -WorkingDirectory $RepoRoot `
            -ArgumentList @("compose", "up", "-d", "--wait", "--wait-timeout", "55", "elasticsearch") `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $esLog "elasticsearch-start.log") `
            -RedirectStandardError (Join-Path $esLog "elasticsearch-start-error.log")
        if (-not $esProcess.WaitForExit(60000)) {
            Write-Warning "ES startup is still running. Keyword retrieval may be unavailable; sync after ES is ready."
        } elseif ($esProcess.ExitCode -ne 0) {
            Write-Warning "ES startup failed; see .runtime/elasticsearch-start-error.log. Other retrieval channels remain available."
        }
    } catch {
        Write-Warning "ES startup failed: $_. Other retrieval channels remain available."
    }
}

Write-Host "[Start] FastAPI -> http://127.0.0.1:8000" -ForegroundColor Green
$fastapiCmd = "cd /d `"$FastApiDir`" && python main.py"
$started += Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $fastapiCmd -WindowStyle Hidden -PassThru

Write-Host "[Start] SpringBoot -> http://127.0.0.1:8080" -ForegroundColor Green
$springCmd = "cd /d `"$SpringDir`" && mvnw.cmd spring-boot:run"
$started += Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $springCmd -WindowStyle Hidden -PassThru

if (-not $SkipFrontend) {
    Write-Host "[Start] Frontend -> http://127.0.0.1:3000" -ForegroundColor Green
    if ($frontendPm -eq "yarn") {
        $frontendCmd = "cd /d `"$FrontendDir`" && yarn start"
    } else {
        $frontendCmd = "cd /d `"$FrontendDir`" && npm run start"
    }
    $started += Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $frontendCmd -WindowStyle Hidden -PassThru
}

$runtimeDir = Join-Path $RepoRoot ".runtime"
New-Item -Path $runtimeDir -ItemType Directory -Force | Out-Null
$pidFile = Join-Path $runtimeDir "started-processes.txt"
$started | ForEach-Object { "$($_.Id)`t$($_.ProcessName)" } | Set-Content -Path $pidFile -Encoding UTF8

Write-Host ""
Write-Host "Application services started in the background." -ForegroundColor Yellow
Write-Host "PIDs saved to: $pidFile"
Write-Host ""
Write-Host "Usage:"
Write-Host "  .\start-all.ps1"
Write-Host "  .\start-all.ps1 -InstallDeps"
Write-Host "  .\start-all.ps1 -SkipFrontend"

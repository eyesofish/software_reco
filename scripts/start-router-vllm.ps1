param(
    [string]$PythonPath = "C:\Users\UYou2\.conda\envs\dech2\python.exe",
    [string]$Model = "Qwen/Qwen3-0.6B-Instruct",
    [string]$ServedModelName = "qwen3-0.6b-instruct-router",
    [string]$Host = "127.0.0.1",
    [int]$Port = 18001,
    [double]$GpuMemoryUtilization = 0.70,
    [int]$MaxModelLen = 4096,
    [string]$AttentionBackend = "TRITON_ATTN",
    [bool]$EnforceEager = $true
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonPath)) {
    Write-Error "Python not found at: $PythonPath"
    exit 1
}

if ($GpuMemoryUtilization -le 0 -or $GpuMemoryUtilization -gt 1) {
    Write-Error "GpuMemoryUtilization must be in (0, 1]."
    exit 1
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    Write-Error "Port $Port is already in use (PID=$($listener.OwningProcess)). Router uses a fixed port and will not override."
    exit 1
}

Write-Host "Starting vLLM router service on $Host`:$Port"
Write-Host "Model=$Model, ServedModelName=$ServedModelName, GPU_MEMORY_UTILIZATION=$GpuMemoryUtilization, ATTN_BACKEND=$AttentionBackend"

$args = @(
    '-m', 'vllm.entrypoints.openai.api_server',
    '--model', $Model,
    '--served-model-name', $ServedModelName,
    '--host', $Host,
    '--port', $Port,
    '--gpu-memory-utilization', $GpuMemoryUtilization,
    '--max-model-len', $MaxModelLen,
    '--dtype', 'auto',
    '--attention-backend', $AttentionBackend,
    '--trust-remote-code'
)

if ($EnforceEager) {
    $args += '--enforce-eager'
}

& $PythonPath @args

#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
MODEL="${MODEL:-Qwen/Qwen3-0.6B-Instruct}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen3-0.6b-instruct-router}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18001}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.70}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
ATTENTION_BACKEND="${ATTENTION_BACKEND:-TRITON_ATTN}"
ENFORCE_EAGER="${ENFORCE_EAGER:-1}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if command -v lsof >/dev/null 2>&1; then
  if lsof -iTCP:"${PORT}" -sTCP:LISTEN -nP >/dev/null 2>&1; then
    echo "Port ${PORT} is already in use. Router uses a fixed port and will not override." >&2
    exit 1
  fi
elif command -v ss >/dev/null 2>&1; then
  if ss -ltn | grep -q ":${PORT} "; then
    echo "Port ${PORT} is already in use. Router uses a fixed port and will not override." >&2
    exit 1
  fi
fi

echo "Starting vLLM router service on ${HOST}:${PORT}"
echo "Model=${MODEL}, ServedModelName=${SERVED_MODEL_NAME}, GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION}, ATTN_BACKEND=${ATTENTION_BACKEND}"

EXTRA_ARGS=()
if [[ "${ENFORCE_EAGER}" == "1" ]]; then
  EXTRA_ARGS+=(--enforce-eager)
fi

exec "${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
  --model "${MODEL}" \
  --served-model-name "${SERVED_MODEL_NAME}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --dtype auto \
  --attention-backend "${ATTENTION_BACKEND}" \
  "${EXTRA_ARGS[@]}" \
  --trust-remote-code

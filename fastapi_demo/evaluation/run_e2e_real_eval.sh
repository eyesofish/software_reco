#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PS1_PATH="$SCRIPT_DIR/run_e2e_real_eval.ps1"

if command -v cygpath >/dev/null 2>&1; then
  PS1_PATH="$(cygpath -w "$PS1_PATH")"
fi

if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -ExecutionPolicy Bypass -NoProfile -File "$PS1_PATH" "$@"
elif command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -File "$PS1_PATH" "$@"
else
  echo "Error: PowerShell not found. Install PowerShell first."
  exit 1
fi

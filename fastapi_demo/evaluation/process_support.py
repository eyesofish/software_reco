"""Stop only child processes started by the evaluation/check runners."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # A Windows virtualenv launcher can own another Python child. Kill the
        # owned tree before killing its parent, including on a check timeout.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)

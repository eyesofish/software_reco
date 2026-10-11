# Subproblem 2: HTTP performance runner

## 1. Goal
Execute 30 fixed cases and compare their timings with an explicit three-round baseline.
## 2. Why this step exists
Existing LangSmith evaluation invokes the graph directly and lacks local HTTP timing gates.
## 3. Files involved
- `fastapi_demo/evaluation/performance_runner.py`: CLI, server lifecycle, scenario execution and reports.
- `fastapi_demo/evaluation/performance_metrics.py`: SSE parser, percentiles and baseline comparison.
- `fastapi_demo/evaluation/performance_app.py`: isolated evaluation bootstrap and offline dependency adapters.
- `fastapi_demo/evaluation/offline_support.py`: loopback-only network guard and deterministic OpenAI adapter.
- `fastapi_demo/app/core/startup.py`: allow the evaluation bootstrap to place runtime log files in its isolated directory.
- `fastapi_demo/evaluation/performance_scenarios.json`: fixed 10 direct, 10 RAG, 5 HITL and 5 memory cases plus corpus.
- `fastapi_demo/tests/test_performance_metrics.py`: deterministic parser and gate tests.
- `fastapi_demo/tests/test_performance_runner.py`: three-round CLI, exit codes, fingerprint redaction and failed-baseline tests.
## 4. Exact changes
Run a managed loopback Uvicorn process with unique Chroma/session/memory/checkpoint/log paths. Prebuild the fixed corpus outside request timing. Disable web, Elasticsearch, image recall and LangSmith in the default profile. Preserve actual model/embedding settings in real mode. Warm up one case per category. Treat SSE errors and missing terminal events as failure. HITL uses the non-streaming first request and streaming confirm; exclude automatic confirmation delay. Memory preparation is separately timed. Write JSON, CSV, Markdown and process logs. Bind baseline compatibility to environment, dependency, corpus and config fingerprint, excluding commit and secret/path values. Gate total scenario P50 and eligible TTFT P50 on both >20% and >1000ms. Generate a baseline only after three valid rounds. Return 0/1/2 for pass/regression/environment failure.
## 5. Out of scope
No Java/browser/load/image evaluation; retain existing 3x100 scripts.
## 6. Done condition
Reports are local and usable without LangSmith, and parser/gate failures have stable exit codes.
## 7. Verification
Run `python -m pytest tests/test_performance_metrics.py`. Execute the 30-case runner with offline adapters through HTTP, then inspect reports. Check real service preflight and exercise real mode if available.
## 8. Expected output
A platform-independent Python CLI and fixed reproducible evaluation data.
## 9. Notes for the next step
PowerShell and CI call this CLI rather than duplicating metrics logic.
## 10. Risks or ambiguity
30-case tail quantiles are descriptive only. First-token absence on final-only memory paths is valid. Baselines must never be promoted implicitly.

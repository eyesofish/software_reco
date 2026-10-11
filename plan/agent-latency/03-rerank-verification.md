# Subproblem 3: Measure and remove repeated neural rerank

## 1. Goal
Measure and remove repeated neural rerank.

## 2. Why this step exists
Reduce measured avoidable latency while keeping evidence and response behavior useful.

## 3. Files involved
fastapi_demo/software_recommend_system/nodes.py; fastapi_demo/software_recommend_system/retriever.py; fastapi_demo/tests/test_latency_optimization.py; fastapi_demo/evaluation/PERFORMANCE.md; fastapi_demo/evaluation/latency_probe.py; .gitignore

## 4. Exact changes
Defer subquery rerank in graph retrieval and rerank once after global dedup. Time model load, model predict and full rerank with document counts/mode. Add reproducible controlled-delay graph probe comparing concurrency 1 vs 3, plus local rerank measurements. Run targeted tests and full offline checks; document real-provider limitation.

## 5. Out of scope
No Java/UI changes, schema migrations, secret edits or silent provider replacement.

## 6. Done condition
New behavior has regression coverage and relevant existing tests pass.

## 7. Verification
From fastapi_demo use `../.venv/Scripts/python.exe -m pytest tests/test_nodes_candidate.py tests/test_latency_optimization.py tests/test_agent_loop.py tests/test_retrieval_timeout.py`; final full offline entry above.

## 8. Expected output
Code, passing regression tests, recorded observations in the plan index.

## 9. Notes for the next step
Do not add nested durations together or call controlled delays real model performance.

## 10. Risks or ambiguity
Concurrency must propagate LangGraph and cancellation context. Local document scores are channel-specific heuristics, not calibrated confidence. Real router previously failed preflight.

Inspection found global rerank bypasses RETRIEVAL_ENABLE_RERANK. Keep lightweight linear global fusion when disabled, but skip cross-encoder. Defer neural rerank in static subquery retrieval and run it once globally when enabled.

Full checks: 297 passed, 1 skipped, HTTP capacity test timed out waiting for startup. Inspect and fix test_performance_http.py client startup to share one AsyncClient outside the occupancy deadline; preserve all capacity, cancellation and subsequent-request assertions. Re-run full checks.

Follow-up failure is different: requests start correctly with shared client, but after-cancel request races worker cleanup. A node span ends before graph checkpoint cleanup and semaphore release. Add execution.cleanup span in execution_control.py after releasing each worker; wait for four cleanup signals in the test before asserting restored capacity. Keep cancellation deadline and assertions unchanged.

Verified by the final shared offline entry: 298 passed, 1 skipped; Ruff passed. See README completion audit and artifact paths.

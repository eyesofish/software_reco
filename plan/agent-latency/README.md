# Task Plan: Agent latency

## Overall goal
Remove unused serial model calls, bound concurrent independent subquery retrieval, search web only when needed, and measure rerank costs without duplicate per-query neural reranking.

## Subproblems
1. `01-model-calls.md` - remove unused generic RAG candidate call - status: verified
2. `02-retrieval.md` - bounded subquery concurrency and demand-driven web - status: verified
3. `03-rerank-verification.md` - one global rerank, stage costs and end-to-end verification - status: verified

## Dependencies
Inspect current answers before removing candidate calls. Then change retrieval. Finally verify all stages together.

## Recommended execution order
1, 2, 3. Preserve recommendation candidates and evidence provenance.

## End-to-end verification
Run `../.venv/Scripts/python.exe -m evaluation.local_checks` from fastapi_demo. Add regression tests and a controlled-latency graph benchmark. Real endpoint timings require healthy providers; never report fake timings as real model speedups.

Step 1 inspection: generic_rag answer_generation consumes evidence independently of candidates. Candidate and greeting-normalization regression tests passed in the full run.

Step 2: 44 targeted tests passed, including actual LangGraph @task fan-out with copied ContextVar, deterministic order and cancellation. Follow-up preserves original request web intent through normalized subqueries; web tool obeys disable switches.

Measured graph probe: evaluation/reports/latency/20261009T223318Z/results.json. Serial/parallel controlled recall P50: 497.5/181.4 ms. Real cached CPU bge rerank: cold 25891.6 ms, warm P50 2667.4 ms for 30 short docs. Real Router re-probe returns fallback at 3479.4 ms, so no successful real HTTP speedup is claimed.


## Final completion audit

Full offline entry completed with exit 0:
`fastapi_demo/evaluation/reports/checks/20261009T224343Z-73b60978/checks.json`.
Ruff passed; 298 tests passed, 1 skipped, 0 failures/errors. Existing strict
mypy issues remain advisory: 190 errors in 26 files, matching the pre-change count.

| Requirement | Current evidence |
| --- | --- |
| Remove unused serial model calls | Generic RAG candidate model is not called; grounded answer still uses evidence. Recommendation model call retained. Greeting normalization skipped; technical greeting normalization retained. test_nodes_candidate.py and test_nodes_normalization.py passed. |
| Bound independent subqueries | Config default 3 (validated 1–8); real graph peak 2 under configured limit 2, stable output order, context and cancellation tests passed. Three-way controlled probe P50 181.4 ms vs serial 497.5 ms. |
| Preserve checkpoint replay | Failed q2 resumed, q1 reused once, original subquery/document mapping retained. test_checkpoint_replay_keeps_subquery_identity passed. |
| Demand-driven web | Strong unique local evidence skips web; absent/weak/duplicate evidence triggers it. Explicit freshness uses web, original opt-out survives normalization, memory excluded as factual coverage, disabled web tool cannot connect, fallback shares deadline. Regression tests passed. Probe always-web calls 18 vs auto 0 including warmup. |
| Check and reduce rerank cost | Static subqueries defer ranking and global dedup scores once; disabled master prevents model inference. Tests passed. Actual cached CPU bge model, 30 short docs: cold 25.9 s, warm P50 2.67 s, linear 8.6 ms. Cold-load/predict/overall spans tested independently. |
| HTTP flow and cleanup | All 30 fixed HTTP/SSE cases passed, plus failures, timeout, 4-worker capacity, cancellation and subsequent successful request. Four execution.cleanup spans prove capacity released. |

Measured artifact: `fastapi_demo/evaluation/reports/latency/20261009T223318Z/results.json`.
Graph comparisons use injected recall delays and a stubbed answer model. Reranker
measurements use real cached local weights. No real HTTP speedup or production
quality guarantee is claimed: configured Router still returns fallback and web
sufficiency uses tunable uncalibrated score heuristics. The implementation goal
is complete; deploying a Router or collecting a new real-model HTTP baseline is
separate work.

# Subproblem 2: Bound retrieval and trigger web on demand

## 1. Goal
Bound retrieval and trigger web on demand.

## 2. Why this step exists
Reduce measured avoidable latency while keeping evidence and response behavior useful.

## 3. Files involved
fastapi_demo/software_recommend_system/nodes.py; fastapi_demo/software_recommend_system/retriever.py; fastapi_demo/app/core/config.py; fastapi_demo/tests/test_latency_optimization.py; fastapi_demo/.env.example; fastapi_demo/software_recommend_system/retrieval_channels.py

## 4. Exact changes
Add validated SUBQUERY_MAX_CONCURRENCY default 3. Register LangGraph tasks in input order in bounded batches; inherit graph context, poll cancellation and preserve result order. Add RECALL_WEB_POLICY auto/always/never; auto launches web for explicit web/freshness queries or insufficient unique local vector/keyword evidence, excludes personal memory as factual coverage. Share one channel timeout across local and fallback web phases. Test bounds, context, cancellation, deterministic ordering, failure, and web decisions.

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

Preserve web intent from the original request using web_query_hint; disable model-driven retrieval for explicit offline requests and enforce web disable switches in recall_web as well.

Use bounded batches of LangGraph tasks registered sequentially on the node thread to preserve checkpoint task IDs; racing threads can change registration order on replay. Extend test_agent_loop.py to invoke a real graph. Verify replay does not swap subqueries.

Verified by the final shared offline entry: 298 passed, 1 skipped; Ruff passed. See README completion audit and artifact paths.

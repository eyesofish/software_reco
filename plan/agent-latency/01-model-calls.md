# Subproblem 1: Remove unused candidate model call

## 1. Goal
Remove unused candidate model call.

## 2. Why this step exists
Reduce measured avoidable latency while keeping evidence and response behavior useful.

## 3. Files involved
fastapi_demo/software_recommend_system/nodes.py; fastapi_demo/tests/test_nodes_candidate.py

## 4. Exact changes
Skip candidate LLM for generic_rag as well as quick_fact_qa; preserve fallback evidence candidates and model candidates for recommendation skills. Test that QA answer still uses evidence and recommendation still calls candidate LLM.

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

Also skip model normalization for known chat-first inputs without technical terms, RAG intent or drawing intent; preserve normalization for technical greetings. Add test_nodes_normalization.py regressions for both branches.

Verified by the final shared offline entry: 298 passed, 1 skipped; Ruff passed. See README completion audit and artifact paths.

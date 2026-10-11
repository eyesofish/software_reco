# Subproblem 1: Request and stage timing

## 1. Goal
Correlate HTTP requests with Agent nodes and existing elapsed-time events.
## 2. Why this step exists
Current local trace IDs do not connect all worker threads to the incoming request.
## 3. Files involved
- `fastapi_demo/software_recommend_system/performance.py`: new span context and JSONL writer.
- `fastapi_demo/app/main.py`: install ASGI timing middleware for full streaming lifetime.
- `fastapi_demo/software_recommend_system/logging_utils.py`: forward elapsed-time events to the timing writer.
- `fastapi_demo/software_recommend_system/rag_agent.py`: time controlled graph nodes.
- `fastapi_demo/software_recommend_system/execution_control.py`: copy context into both worker submissions.
- `fastapi_demo/software_recommend_system/retriever.py`: copy context per recall task and time reranking.
- `fastapi_demo/tests/test_performance_timing.py`: test streaming lifetime, concurrent correlation and disabled tracing.
## 4. Exact changes
Add optional JSONL tracing selected by PERFORMANCE_TRACE_FILE. Keep existing response bodies valid; add X-Request-Id. Record monotonic start/end, parent span, request/run identifiers, stage, result and milliseconds. Wrap actual ASGI send/receive rather than BaseHTTPMiddleware. Propagate a fresh copied context per thread task. Expose node result mode for branch assertions without recording query text or secrets.
## 5. Out of scope
No new external tracing dependency or endpoint; no edits to routing or chunking behavior.
## 6. Done condition
Parallel spans are attributed to their own requests and streams close their HTTP span at the final body.
## 7. Verification
Run `python -m pytest tests/test_performance_timing.py tests/test_execution_control.py tests/test_retrieval_timeout.py` from fastapi_demo.
## 8. Expected output
Optional local structured timing data and passing focused tests.
## 9. Notes for the next step
Runner sends request IDs and reads only that run's JSONL file.
## 10. Risks or ambiguity
Late cancelled workers must retain the original request context; instrumentation must not hide application errors or change cancellation semantics.

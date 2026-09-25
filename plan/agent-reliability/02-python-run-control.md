# Subproblem 2: Control Python recommendation runs

## 1. Goal
Enforce request deadlines, reject excess active graph work, stop cooperative graph progress on cancellation, and return an explicit failure state.

## 2. Why this step exists
The current graph checks elapsed time only between retrieval rounds and can generate a success response after its budget has expired.

## 3. Files involved
- `fastapi_demo/software_recommend_system/state.py` and `rag_agent.py` - existing session and graph nodes, plus explicit recursion protection.
- `fastapi_demo/software_recommend_system/execution_control.py` - run identity, deadline, cancellation event, concurrency gate, and control exceptions.
- `fastapi_demo/app/api/v1/stream_utils.py` and `handlers/recommend_orchestrator.py` - controlled invocation and response shaping.
- LLM client factories and graph nodes - propagate remaining timeout and stop checks into external calls and node loops.

## 4. Exact changes
- Create a fresh run control for each invocation, keyed to the existing session only while that run is active; exclude human confirmation pause time and create a fresh deadline on resume.
- Execute synchronous LangGraph work on one four-worker executor. Keep a worker occupied until its synchronous invocation exits, even when its HTTP caller has stopped waiting; reject work when capacity is exhausted.
- Wrap every graph node with checks before and after execution; expose the active run control to nested model/tool helpers, add cooperative checks to long loops, and cap outbound client timeout to remaining time with SDK retries disabled for controlled calls.
- Map timeout, caller cancellation, graph recursion exhaustion, and ordinary failures to distinct internal reasons. Apply an explicit graph `recursion_limit` of 64.
- Leave existing successful and HITL response shapes valid; include optional run id, stop reason, and elapsed time, and emit `failed` for failed turns.

## 5. Out of scope
Do not introduce token accounting, automatic model switching, new database status values, or claims that Python can forcibly kill a running thread or remote provider request.

## 6. Done condition
Slow runs stop creating new downstream work, deadlines apply to graph invocation, overflow is bounded, and failed or late results cannot be returned or persisted as success.

## 7. Verification
Unit-test expired deadlines, model/tool exceptions, node cancellation, recursion-limit errors, bounded saturation, and permit release after a cancelled caller's synchronous node exits.

## 8. Expected output
Reusable Python execution control and backward-compatible response metadata.

## 9. Notes for the next step
Streaming should consume the same control object and terminal reasons rather than implementing separate budget semantics.

## 10. Risks or ambiguity
An already-running synchronous call can only stop through its network timeout or an explicit cancellation API; its worker remains occupied until it exits.

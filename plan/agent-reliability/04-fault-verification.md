# Subproblem 4: Verify fault paths and record results

## 1. Goal
Prove normal behavior, timeout, cancellation, failure, HITL resume, and benchmark reporting using repeatable checks.

## 2. Why this step exists
A resume claim about reliability needs tests and recorded measurements; implementation details alone are not evidence.

## 3. Files involved
- `fastapi_demo/tests/` - Python execution and HTTP-stream fault tests.
- `ollama_springboot/demo/src/test/` - focused Spring task/stream tests if existing seams make them practical.
- `ollama_springboot/ollama-gui-reactjs/src/` - requester or UI tests for incomplete and terminal events.
- `.github/workflows/ci.yml` - align Java CI with the project Java 21 requirement.
- `story/` and `plan/agent-reliability/README.md` - final observed results and task statuses.

## 4. Exact changes
- Add deterministic tests using fake nodes and fake providers, never real paid model calls.
- Assert a 1-second budget returns control within 2 seconds, stops downstream invocations, excludes late success, and releases capacity after the underlying synchronous work exits.
- Exercise normal final, timeout, disconnect, partial-stream close, exception, recursion limit, session contention, HITL resume, and terminal-state idempotence.
- Run the Python, Java, and frontend CI-equivalent checks; record any environment limitation accurately and align Java CI to 21.
- Record commands, outcomes, run and cancellation timings, and additional downstream call count; update resume text only with passing observed results.

## 5. Out of scope
Do not fabricate production traffic metrics or a model-quality improvement.

## 6. Done condition
All focused fault tests pass, broader checks pass or have a recorded reproducible external blocker, and every resume metric is supported by a checked-in run output.

## 7. Verification
Use `python -m pytest tests/ -v`, `mvnw.cmd -B verify -DskipTests=false`, and `npm test -- --watchAll=false` plus `npm run build` from their respective service roots.

## 8. Expected output
Passing fault tests, updated CI/runtime documentation, and an evidence-backed interview snapshot.

## 9. Notes for the next step
Future token-budget or model-routing work can build on the run control and evaluation vocabulary established here.

## 10. Risks or ambiguity
Frontend package manager and CI Java configuration may differ from the local workstation; report the commands actually run.

## 11. Observed results
- `python -m pytest tests -q` from `fastapi_demo`, after rebasing onto remote master: 180 passed. The environment emitted a LangSmith 403 while trying to upload traced runs; it did not fail the suite.
- `python -m ruff check software_recommend_system/execution_control.py tests/test_execution_control.py --output-format concise`: passed.
- `npm test -- --watchAll=false --runInBand`: 3 passed; `npm run build`: passed.
- `mvnw.cmd -q test` from `ollama_springboot/demo`: passed on local JDK 24 after explicit Lombok annotation processing. CI was aligned to the project's Java 21 setting.
- The 30 ms deadline test asserts return under 200 ms; its simulated synchronous operation lasts 200 ms, so the caller returns before that work exits. Cancellation is cooperative; this does not prove forcible thread termination or provider-side cancellation.
- Not covered: an actual HTTP disconnect propagated across all three services, HITL pause/resume, recursion-limit exhaustion, concurrency saturation, and late provider calls/billing.

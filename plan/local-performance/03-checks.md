# Subproblem 3: Local checks and CI

## 1. Goal
Provide a single local entry point and test the real HTTP/SSE behavior with offline dependencies.
## 2. Why this step exists
Functional checks, timings and artifacts must be repeatable locally and in CI.
## 3. Files involved
- `scripts/run_checks.ps1`: root-relative entry point with Python selection and exit-code forwarding.
- `fastapi_demo/evaluation/local_checks.py`: offline ruff/mypy/pytest orchestration and summary.
- `fastapi_demo/evaluation/process_support.py`: terminate the owned process tree on Windows and prevent advisory-timeout children from leaking.
- `fastapi_demo/tests/test_performance_http.py`: managed-server success, failure, timeout, cancellation and capacity tests.
- `fastapi_demo/tests/conftest.py`: opt-in offline network isolation and unique state directories.
- `.github/workflows/ci.yml`: reuse offline entry and upload artifacts; preserve Java job.
- `.github/workflows/performance.yml`: manual real/baseline workflow on a configured self-hosted machine.
- `fastapi_demo/evaluation/PERFORMANCE.md`: commands, metrics, baseline and environment requirements.
- `.gitignore`: track task plans/documentation while keeping generated data ignored.
- `fastapi_demo/app/api/v1/handlers/recommend_orchestrator.py`, `fastapi_demo/app/api/v1/routes.py`, `fastapi_demo/app/api/v1/stream_utils.py`, `fastapi_demo/software_recommend_system/nodes.py`: clear existing lint errors in the tested request chain by sorting imports, removing an unused exception binding and modernizing type aliases only.
## 4. Exact changes
Run lint and pytest as hard gates and mypy as advisory. Save JUnit, slowest 20 cases, stage logs and a summary even on failure. Never pipe a command so its nonzero status is lost. Block external network in offline pytest and server, allow loopback and report Elasticsearch skips. Validate process cleanup and isolate all runtime state. CI uploads artifacts even when tests fail. A manually triggered self-hosted real run downloads an explicitly named baseline artifact or records a new three-round baseline.

The first complete run found 13 existing lint errors in this request chain, plus three import/line-length issues introduced during context propagation. Fix only these mechanical issues so the shared hard gate can pass. Bound advisory mypy to 120 seconds so external-library analysis cannot prevent test/report generation.
## 5. Out of scope
Do not fix unrelated repository-wide lint/type issues by mass reformatting. Report existing failures accurately.
## 6. Done condition
The root PowerShell command forwards correct exit codes and CI uses the same Python workflow.
## 7. Verification
Run new tests, then `scripts/run_checks.ps1 -Mode offline` using the available Python interpreter. Inspect artifacts and git diff. Verify real-mode missing/mismatched baseline exits 2.
## 8. Expected output
Runnable commands, tests, documented semantics and artifact-producing CI.
## 9. Notes for the next step
Mark plan steps verified only with concrete evidence; distinguish live performance results from synthetic validation.
## 10. Risks or ambiguity
The repository may already fail global lint; do not silently ignore it. Real hosted runners cannot reuse a local-machine baseline.

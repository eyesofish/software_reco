# Task Plan: Local checks and performance regression

## Overall goal
Implement the approved offline, baseline and real HTTP/SSE checks for FastAPI and Agent. Keep existing work and historical evaluation intact.

## Subproblems
1. `01-timing.md` - request and stage correlation - status: verified
2. `02-runner.md` - fixed scenarios, isolated servers, reports and baseline gates - status: verified
3. `03-checks.md` - offline tests, local entry point, CI and documentation - status: verified

## Dependencies
The runner consumes timing records from step 1. Step 3 verifies both earlier steps and exposes the same commands locally and in CI.

## Recommended execution order
Complete 1, then 2, then 3. Preserve the user's uncommitted changes in shared files.

## End-to-end verification
Use the available Python environment to run focused pytest tests, then the full offline entry point. Verify baseline math with synthetic data. Run real evaluation when configured services are reachable; never substitute offline measurements for real results.

## Verification evidence
- Timing/control/timeout: 15 passed using the isolated Python 3.11 environment.
- Metrics: 10 passed; 30-case offline HTTP runner passed all formal cases and warmups.
- HTTP integration: 34 passed, including failure, timeout, capacity and cancellation.
- Final root PowerShell offline entry: exit 0; full Ruff passed; 270 tests passed, 1 live Elasticsearch test skipped. Type checking completed with 190 advisory errors in 26 files; no errors were reported for the new timing module.
- Final report: `fastapi_demo/evaluation/reports/checks/20261009T183924Z-276e074e/report.md` (generated/ignored).
- Three-round baseline CLI and P50 regression flow verified with explicitly synthetic unit data. Missing baseline through the PowerShell entry returned exit 2 before any model call.
- Real baseline setup used the configured embedding path but stopped because the configured router returned a fallback/unavailable result. Report `fastapi_demo/evaluation/reports/performance/20261009T183334Z-72036895/report.md` has exit 2 and no baseline was created. Restore the configured router before recording a genuine baseline.
- Both workflow YAML files parsed successfully. CI definitions were authored locally, not remotely triggered.

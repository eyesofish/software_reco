# 5. Verification and interview handoff

## Goal and why
Back the architecture explanation with repeatable behavioral evidence.
## Files involved and exact changes
Java integration tests use real login/JWT/database and a controlled HTTP Agent stub; test owned CRUD, task aliases, denied upstream calls, SSE/HITL and service headers. Python tests cover fail-closed auth and session memory separation. Frontend tests cover authentication/caches/stream cancellation/blobs. Add a runnable offline two-user verification script and story evidence document, update README startup instructions.
## Out of scope
Performance/scaling claims without measurement.
## Done condition and verification
Required suites/build pass; deterministic HTTP demo reports successful and denied scenarios, saves non-sensitive results. Run actual model smoke only if configured and distinguish it from stub evidence.
## Expected output
30-second/2-minute Chinese story, English short version, architecture diagram and skeptical follow-up answers. Explain why FastAPI could also own auth and the costs of two services; mark scaling as future work.
## Risks
Never claim production readiness, cross-instance safety or real model performance from stub tests.

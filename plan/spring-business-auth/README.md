# Spring business authentication

## Overall goal
Make Spring the authenticated business boundary, with two demo users, private conversations/tasks, internal Agent authentication, and a usable login UI. FastAPI remains the execution service. Independent horizontal scaling is future work.

## Subproblems
1. `01-login.md` — users and JWT — status: verified
2. `02-ownership.md` — owned conversations/tasks — status: verified
3. `03-agent-auth.md` — internal credentials and startup — status: verified
4. `04-ui.md` — login, cache and stream isolation — status: verified
5. `05-verification.md` — tests, demo and interview evidence — status: verified

## Dependencies and execution order
Run 1 → 2 → 3 → 4 → 5. Verify each checkpoint before marking verified. Preserve anonymous legacy data without assigning it. No registration, refresh tokens, role hierarchy, checkpoint migration, or multi-instance claims.

## End-to-end verification
Java: `./mvnw.cmd test` from the backend. Python: project venv `python -m pytest tests/test_auth_middleware.py tests/test_execution_control.py` and memory isolation tests. Frontend: `npm test -- --watchAll=false --runInBand`, `npm run build`. Offline integration uses an HTTP Agent stub, checks both owners and SSE/HITL, and records real results. Real-provider behavior is explicitly separate.

## Implementation refinements
- Main chat SSE now creates real persisted tasks and validates confirmation lifecycle before resume.
- Token deltas preserve all whitespace; the HTTP demo exposed trimming in the existing normalization.
- Dedicated Agent clients carry service credentials; Security owns CORS configuration.
- The demo runner waits for both seeded accounts and launches the actual JVM executable so cleanup works on Windows.
- Browser verification passed login, streamed answer, reload, A→B isolation, logout and 390px layout checks. Screenshots are in ignored `.runtime/`.

## Verified results (2026-10-12)
- Java: 14 tests passed, including 8 HTTP integration tests.
- Python: 20 focused auth/execution/memory tests passed with external networking blocked.
- Frontend: 11 tests in 4 suites passed; production build passed.
- Independent HTTP demo: 16 checks passed; all 3 Agent calls carried the service key.
- Ruff checks, Git whitespace check and Compose configuration validation passed.
- Code review checked ownership before side effects, task aliases, missing JWT claims, CORS, stale frontend callbacks and credential logging. Removed unused internal conversation-creation shim.
- PostgreSQL migration execution, real model behavior and multi-instance deployment were not tested in this task.

TASK:/d/Github/software_reco/ollama_springboot/ollama-gui-reactjs
Fix the most fragile part first: HITL (human confirmation) state-machine consistency.

Goals:

- The frontend must reliably show the confirmation panel during `PENDING_CONFIRM`.
- Final results must be shown only when the task is actually completed.
- Do not misclassify "waiting for confirmation" text as `DONE`.

STEP 1 — Unify state semantics (define rules first, then change code)

1. Define one canonical state flow:
   `IDLE -> GENERATING -> PENDING_CONFIRM -> GENERATING -> DONE/FAILED/EXPIRED`
2. Define field semantics clearly:
   - `sub_questions`: meaningful only in `PENDING_CONFIRM`.
   - `final_result`: meaningful only in `DONE`.
   - `error_message`: meaningful only in failure states (`FAILED/EXPIRED`).
3. Constraint: any non-`DONE` status must never be rendered as a completed state in frontend UI.

STEP 2 — Fix Spring task-state persistence logic

1. Update `RecommendTaskStateService` persistence behavior:
   - When status is `PENDING_CONFIRM`, do not persist `finalResult` (clear it).
   - When status is `GENERATING`, do not persist `finalResult` (clear it).
   - Persist `finalResult` only when status is `DONE`.
2. Adjust `resolveStatusFromFastApi` related branches to ensure:
   - `awaiting=true` or pending sub-questions => `PENDING_CONFIRM`.
   - non-terminal state with no final answer => `GENERATING`.
3. Prevent stale-value contamination:
   - If status moves from `DONE` back to a non-terminal state (for example, recovery paths), force-clear `finalResult`.

STEP 3 — Fix React task-status interpretation

1. Update `normalizeTaskStatus` in `useChatLogic.ts`:
   - Do not classify as `DONE` just because `finalResult` is non-empty.
   - Determine frontend state from backend status first.
2. Recommended frontend priority:
   - `PENDING_CONFIRM` -> show confirmation panel.
   - `GENERATING/CONFIRMING` -> show processing UI.
   - `DONE` with non-empty `finalResult` -> show final result.
   - `FAILED/EXPIRED` -> show error state.
3. Keep `buildSnapshotFromTask` consistent:
   - Clear `subQuestions` when status is not `PENDING_CONFIRM`.
   - Clear `finalResult` when status is not `DONE`.

STEP 4 — Verification (must run in order)

1. Backend test/build check:
   - `cd D:\Github\software_reco\ollama_springboot\demo`
   - `./mvnw test`
2. Frontend type check:
   - `cd D:\Github\software_reco\ollama_springboot\ollama-gui-reactjs`
   - `npx tsc --noEmit`
3. Manual integration check (critical):
   - Send a recommendation request and verify it enters `PENDING_CONFIRM`.
   - Click Confirm and verify it enters `GENERATING`.
   - Poll until `DONE`, then verify final answer is rendered.
   - Repeat once with `edit -> confirm` and verify it does not jump to `DONE` early.

STEP 5 — Completion criteria (all required)

1. During `PENDING_CONFIRM`, the confirmation panel is always visible.
2. During `GENERATING`, no "final result" is displayed.
3. During `DONE`, exactly one assistant final message is appended (no duplicates).
4. Spring tests pass and frontend type check passes.
5. Logs no longer show invalid transitions where "confirming" is treated as "completed".

STOP after completion.

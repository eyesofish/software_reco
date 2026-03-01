TASK:
Fix the HITL confirm button disabled issue after sub-questions are generated.

Problem confirmation:

The issue is real.

Current logic in `ConfirmationPanel`:
- `canConfirm = !!taskId && !loading`
- button text: `loading ? 'Confirming...' : 'Confirm and Continue'`

This is wrong because `loading` is a global chat-level flag, not a confirm-action-only flag.
So when task status is already `PENDING_CONFIRM`, the button can still be disabled by unrelated loading states.

Root cause:
- Confirm button state is coupled to global `loading`.
- Global `loading` is updated by multiple flows (`loadConversationState`, request bootstrap, task sync), not only by confirm submit.

STEP 1 — Decouple button state from global loading

1. In chat page state mapping, treat confirm state independently:
   - `isConfirming = taskStatus === 'CONFIRMING'`
2. Pass `isConfirming` to `ConfirmationPanel` instead of using `loading` for button enablement.
3. Keep global `loading` only for page-level spinner rendering, not for confirm button disable logic.

STEP 2 — Update ConfirmationPanel behavior

1. Replace:
   - `canConfirm = !!taskId && !loading`
2. With:
   - `canConfirm = !!taskId && !isConfirming`
3. Replace button label condition:
   - from `loading ? 'Confirming...' : 'Confirm and Continue'`
   - to `isConfirming ? 'Confirming...' : 'Confirm and Continue'`
4. Optional hardening:
   - if `!taskId`, show small hint text like `Task id missing, please refresh task state`.

STEP 3 — Stabilize snapshot loading semantics for PENDING_CONFIRM

1. In snapshot builder (`buildSnapshotFromTask`), enforce:
   - if backend status is `PENDING_CONFIRM`, snapshot `loading` should be `false`.
2. Ensure no later patch sets `loading=true` while status remains `PENDING_CONFIRM`.
3. Keep `CONFIRMING` as the only state that disables the confirm button.

STEP 4 — Prevent accidental double submit

1. Add a lightweight in-flight guard (ref or local state) in confirm handler.
2. Ignore extra clicks while confirm request is in progress.
3. Release guard when request resolves or fails.

STEP 5 — Verification checklist

1. Reproduce path:
   - send query -> receive `PENDING_CONFIRM` with sub-questions.
2. Verify:
   - button text is `Confirm and Continue`.
   - button is clickable.
3. Click confirm:
   - button becomes disabled with text `Confirming...`.
   - status transitions to `GENERATING`.
4. Final:
   - task reaches `DONE`.
   - no stuck disabled button in `PENDING_CONFIRM`.

Completion criteria:
- In `PENDING_CONFIRM`, confirm button is enabled whenever `taskId` exists.
- Button is disabled only during actual confirm submission (`CONFIRMING`).
- No regression in polling, task transitions, or final message rendering.

STOP after completion.

TASK:
Scope the top-right loading image to backend-waiting only.

Element location (confirmed):

1. Render site:
   - `ollama_springboot/ollama-gui-reactjs/src/pages/chat/index.tsx`
   - current render condition:
     `(loading || showConfirming) && <Loading src={LOADING} />`
2. Style definition:
   - `ollama_springboot/ollama-gui-reactjs/src/pages/chat/style.ts`
   - `Loading = styled.img`
3. Note:
   - `sc-jsEegq hxOmVJ` is a runtime-generated styled-components class name.
   - It is not a stable selector and should not be used as a logic anchor.

Problem:

The loading image is currently driven by a generic `loading` flag.
That flag is reused by bootstrap/sync flows and can become true even when the UI is not actually waiting for backend output.

Expected behavior:

The loading image should appear only when the current chat is waiting for backend results:

- sending recommend request,
- confirming HITL action,
- polling while task is `GENERATING`.

It should NOT appear in:

- plain `PENDING_CONFIRM` waiting state (user action required),
- local hydration/bootstrap that does not represent active backend output waiting.

STEP 1 — Define explicit backend-waiting states

1. Add/derive a dedicated flag:
   - `isWaitingBackendOutput`
2. Recommended source:
   - `taskStatus === 'CONFIRMING'`
   - `taskStatus === 'GENERATING'`
   - plus optional short-lived request flag for initial create-request round trip.

STEP 2 — Decouple spinner from generic loading

1. Keep `loading` for local page/snapshot hydration if needed.
2. Do not use `loading` to control `<Loading />`.
3. Replace render condition in chat page:
   - from `(loading || showConfirming)`
   - to `isWaitingBackendOutput`.

STEP 3 — Keep PENDING_CONFIRM interactive and clean

1. When status is `PENDING_CONFIRM`:
   - spinner hidden,
   - confirm button enabled (except actual confirm submit in-flight),
   - sub-question panel visible.

STEP 4 — State-transition constraints

1. Send query:
   - waiting indicator ON while request is in-flight.
2. Backend returns `PENDING_CONFIRM`:
   - waiting indicator OFF immediately.
3. Click confirm:
   - waiting indicator ON (`CONFIRMING` then `GENERATING`).
4. Task reaches `DONE`/`ERROR`:
   - waiting indicator OFF.

STEP 5 — Verification checklist

1. Open an existing conversation with pending questions:
   - spinner is hidden.
2. Click confirm:
   - spinner appears.
3. During backend generation:
   - spinner remains visible.
4. On completion/error:
   - spinner disappears.
5. Switching chats does not keep stale spinner state.

Completion criteria:

- The loading image appears only during real backend output waiting.
- No spinner during passive `PENDING_CONFIRM`.
- No regressions in HITL confirm flow or task polling.

STOP after completion.

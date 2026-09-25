# Subproblem 3: Propagate streaming stop and failure states

## 1. Goal
Make stream disconnects and backend failures stop future work and appear as incomplete/error outcomes in the browser.

## 2. Why this step exists
The browser currently treats a closed stream with partial text as completed, and Spring's blocking SSE bridge does not cancel its FastAPI subscription when the client disconnects.

## 3. Files involved
- `fastapi_demo/app/api/v1/routes.py` - request disconnect monitoring and terminal SSE events.
- `ollama_springboot/demo/src/main/java/com/example/demo/client/FastApiClient.java`, controllers, and task-state service - cancellable upstream subscription, heartbeats, and terminal failure handling.
- `ollama_springboot/ollama-gui-reactjs/src/services/requester.ts` and `pages/chat/useChatLogic.ts` - incomplete-stream handling and visible task state.

## 4. Exact changes
- Run route stream generation as a cancellable task, observe the ASGI request disconnect, and set the Python run cancellation signal before cancelling the task.
- Keep `final` exclusive to successful completion; send a structured `error` for timeouts and runtime failures. Closing an already disconnected response must not attempt another terminal write.
- In Spring, use a cancellable WebClient `Disposable`, cancel it on emitter completion/error/timeout or failed sends, send an ignored comment heartbeat every two seconds, and prevent terminal database states from being overwritten by late events.
- Preserve partial browser text after an abort or incomplete close while marking the message/task incomplete; append a final assistant response only after a genuine final event.

## 5. Out of scope
Do not add a public cancel endpoint or database migration. Preserve endpoint routes and existing event names.

## 6. Done condition
Closing the browser request reaches the Python run-control event, stops downstream graph progress when the current blocking call returns, and never leaves partial output labeled DONE.

## 7. Verification
Test request aborts with a fake stream, Spring event cancellation and terminal-state races where test seams exist, and browser handling of final/error/abrupt-close sequences.

## 8. Expected output
End-to-end cancellation and honest streaming terminal states.

## 9. Notes for the next step
Verification evidence should state when cancellation was signalled, when execution actually exited, and whether late work occurred.

## 10. Risks or ambiguity
Servlet disconnect detection depends on a failed write or heartbeat; an active upstream network call may continue until its configured timeout.

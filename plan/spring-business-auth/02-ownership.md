# 2. Business ownership

## Goal and why
Prevent an authenticated user from operating another user's conversations, tasks, facts or Agent session.
## Files involved and exact changes
Conversation entity/service/repositories: set owner on every creation, scope list and lookup. Controllers pass principal UUID explicitly. Task service resolves IDs through an ownership facade before lifecycle mutations or upstream calls. Messages/facts/tasks inherit conversation ownership. Async callbacks capture an already authorized immutable owner/conversation context; internal maintenance never uses request ThreadLocal identity.
## Out of scope
Legacy import and multi-instance coordination.
## Done condition and verification
A's IDs return 404 to B on CRUD, chat/stream, state, task aliases and confirm/edit; no upstream request or database mutation occurs. Legacy rows stay unowned/inaccessible. Deleted IDs cannot be recreated.
## Expected output and next step
Business authorization independent of Agent implementation.
## Risks and refinement
Existing ensureConversation creates arbitrary client IDs. Unknown nonempty IDs will now return 404; only missing IDs create server UUIDs. This prevents resurrecting orphan/legacy FastAPI memory. Validate all supplied conversation/session/task aliases, not only the first nonblank ID.

TASK:
Make chat requests isolated per chat session.

Each chat must maintain its own loading state.

STEP 1 — Modify chats structure

Change from:

chats = [
{ id, messages }
]

To:

chats = [
{ id, messages, loading }
]

STEP 2 — Modify sendMessage

Change from:

setLoading(true)

To:

setChatLoading(chatId, true)

STEP 3 — When response arrives:

setChatLoading(chatId, false)

STEP 4 — Ensure switching chats does not interrupt other chat loading.

STOP after completion.

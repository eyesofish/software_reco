TASK:
Allow user to switch chats or create new chat while another chat is loading.

Requirements:

- sendMessage must be tied to chatId
- do not use global loading state
- each chat has independent loading flag

STEP 1 — ensure sendMessage(chatId, message)

STEP 2 — ensure response updates only that chat

STEP 3 — ensure UI reads messages from currentChatId

STOP after completion.

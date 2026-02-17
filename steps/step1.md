You are a senior React engineer.

TASK:
Fix chat deletion so that when the currently active chat is deleted, the main message view clears immediately.

Current problem:
Messages from deleted chat still render.

STEP 1 — Locate currentChatId state

Find state like:

currentChatId

STEP 2 — Modify deleteChat function

Change from:

deleteChat(chatId)

To:

deleteChat(chatId) {
remove chat from chats store

if (currentChatId === chatId) {
setCurrentChatId(null)
}
}

STEP 3 — Modify message rendering

Ensure rendering logic is:

const messages = chats[currentChatId]?.messages || []

STEP 4 — Show modified files

STOP after completion.

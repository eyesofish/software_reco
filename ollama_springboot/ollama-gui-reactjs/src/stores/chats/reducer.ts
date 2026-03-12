import StoreAction from '~/entities/storeAction'
import { STATE } from './index'

export default function reducer (
  state : typeof STATE, action : StoreAction
) {
  switch (action.type) {
    case 'ADD_MESSAGE': {
      const { index, message } = action.payload
      const nextChats = [...state.chats]
      const nextMessages = Array.isArray(nextChats[index])
        ? [...nextChats[index], message]
        : [message]
      nextChats[index] = nextMessages
      return { chats: nextChats }
    }
    case 'UPDATE_MESSAGE_CONTENT': {
      const { index, time, content, conversationId, sessionId } = action.payload
      const existing = Array.isArray(state.chats[index]) ? state.chats[index] : []
      if (existing.length === 0) return state

      const targetIndex = existing.findIndex((message) => Number(message.time) === Number(time))
      if (targetIndex < 0) return state

      const updatedMessage = {
        ...existing[targetIndex],
        content,
        conversationId: conversationId ?? existing[targetIndex].conversationId,
        sessionId: sessionId ?? existing[targetIndex].sessionId
      }
      const nextMessages = [...existing]
      nextMessages[targetIndex] = updatedMessage

      const nextChats = [...state.chats]
      nextChats[index] = nextMessages
      return { chats: nextChats }
    }
    case 'DELETE_CHAT': {
      const nextChats = state.chats.filter((_, index) => index !== action.payload)
      if (nextChats.length === state.chats.length) return state
      return { chats: nextChats }
    }
    case 'LOAD_CHATS': {
      return { chats: [...action.payload] }
    }
    default: return state
  }
}

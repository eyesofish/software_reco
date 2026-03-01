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

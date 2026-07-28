import { Chats, Message, RetrievedImage } from '~/entities/messages'

export function addMessage (payload : { index : number, message : Message }) {
  return { type: 'ADD_MESSAGE', payload }
}

export function updateMessageContent (payload : {
  index : number,
  time : number,
  content : string,
  conversationId ?: string,
  sessionId ?: string,
  retrievedImages ?: RetrievedImage[]
}) {
  return { type: 'UPDATE_MESSAGE_CONTENT', payload }
}

export function deleteChat (payload : number) {
  return { type: 'DELETE_CHAT', payload }
}

export function loadChats (payload : Chats) {
  return { type: 'LOAD_CHATS', payload }
}

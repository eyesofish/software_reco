import { Message } from '~/entities/messages'


export default function createUserMessage (
  content : string,
  conversationId ?: string,
  sessionId ?: string
) : Message {
  return {
    role: 'user',
    content,
    time: Date.now(),
    conversationId,
    sessionId
  }
}

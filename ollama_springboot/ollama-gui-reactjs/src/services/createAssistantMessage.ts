import { Message } from '~/entities/messages'


export default function createAssistantMessage (
  content : string,
  conversationId ?: string,
  sessionId ?: string
) : Message {
  return {
    role: 'assistant',
    content,
    time: Date.now(),
    conversationId,
    sessionId
  }
}

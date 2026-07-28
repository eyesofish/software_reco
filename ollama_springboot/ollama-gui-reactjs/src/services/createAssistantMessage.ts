import { Message, RetrievedImage } from '~/entities/messages'


export default function createAssistantMessage (
  content : string,
  conversationId ?: string,
  sessionId ?: string,
  retrievedImages : RetrievedImage[] = []
) : Message {
  return {
    role: 'assistant',
    content,
    time: Date.now(),
    conversationId,
    sessionId,
    retrievedImages
  }
}

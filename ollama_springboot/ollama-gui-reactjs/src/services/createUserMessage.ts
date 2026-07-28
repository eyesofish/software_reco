import { ImageAttachment, Message } from '~/entities/messages'


export default function createUserMessage (
  content : string,
  conversationId ?: string,
  sessionId ?: string,
  images : ImageAttachment[] = []
) : Message {
  return {
    role: 'user',
    content,
    time: Date.now(),
    conversationId,
    sessionId,
    images
  }
}

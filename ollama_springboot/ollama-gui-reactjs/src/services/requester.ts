import { Messages, ModelResponse } from '~/entities/messages'

function resolveConversationId (messages : Messages) : string|undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    const candidate = message.conversationId || message.sessionId
    if (candidate && candidate.trim()) {
      return candidate.trim()
    }
  }
  return undefined
}


export default async function requester (
  modelUrl : string,
  modelName : string,
  messages : Messages,
  conversationId : string|undefined,
  onData : (response : ModelResponse) => void,
  onError : (error : unknown) => void
) {
  try {
    const payloadMessages = messages.map(({ role, content }) => ({ role, content }))
    const effectiveConversationId = conversationId || resolveConversationId(messages)
    const payload : {
      model: string,
      messages: Array<{ role: string, content: string }>,
      conversation_id?: string
    } = {
      model: modelName,
      messages: payloadMessages
    }
    if (effectiveConversationId) {
      payload.conversation_id = effectiveConversationId
    }

    const response = await fetch(modelUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })
    const decoder = new TextDecoder()
    response.body?.pipeTo(new WritableStream({
      write: chunk => {
        try {
          onData(JSON.parse(decoder.decode(chunk)))
        }
        catch (error) {
          onError(error)
        }
      }
    }))
  }
  catch (error) {
    onError(error)
  }
}

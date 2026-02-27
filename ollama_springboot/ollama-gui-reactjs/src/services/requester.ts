import { ConfirmPayload, Messages, ModelResponse } from '~/entities/messages'

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
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    const responsePayload = await response.json() as ModelResponse
    onData(responsePayload)
  }
  catch (error) {
    onError(error)
  }
}

export function resolveConfirmUrl (chatUrl : string) : string {
  const trimmed = chatUrl.trim().replace(/\/+$/, '')
  if (trimmed.endsWith('/api/chat')) {
    return `${trimmed}/confirm`
  }
  return `${trimmed}/confirm`
}

export async function confirmRequester (
  confirmUrl : string,
  payload : ConfirmPayload
) : Promise<ModelResponse> {
  const response = await fetch(confirmUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return await response.json() as ModelResponse
}

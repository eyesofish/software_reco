import { ConfirmPayload, Messages, ModelResponse, RecommendTaskStateResponse, SessionStateResponse } from '~/entities/messages'

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

export function resolveSessionStateUrl (chatUrl : string, sessionId : string) : string {
  const trimmed = chatUrl.trim().replace(/\/+$/, '')
  const encodedSessionId = encodeURIComponent(sessionId)

  if (/\/api\/chat(?:\/confirm)?$/.test(trimmed)) {
    const base = trimmed.replace(/\/api\/chat(?:\/confirm)?$/, '')
    return `${base}/api/session-state/${encodedSessionId}`
  }

  if (/\/api\/v1\/recommend(?:\/confirm)?$/.test(trimmed)) {
    const base = trimmed.replace(/\/api\/v1\/recommend(?:\/confirm)?$/, '')
    return `${base}/api/v1/session-state/${encodedSessionId}`
  }

  if (trimmed.endsWith('/api/session-state') || trimmed.endsWith('/api/v1/session-state')) {
    return `${trimmed}/${encodedSessionId}`
  }

  return `${trimmed.replace(/\/api\/chat\/confirm$/, '').replace(/\/api\/chat$/, '')}/api/v1/session-state/${encodedSessionId}`
}

export async function getSessionState (
  chatUrl : string,
  sessionId : string,
  signal ?: AbortSignal
) : Promise<SessionStateResponse> {
  const response = await fetch(resolveSessionStateUrl(chatUrl, sessionId), {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json'
    },
    signal
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return await response.json() as SessionStateResponse
}

export function resolveTaskStateUrl (chatUrl : string, taskId : string) : string {
  const trimmed = chatUrl.trim().replace(/\/+$/, '')
  const encodedTaskId = encodeURIComponent(taskId)

  if (/\/api\/chat(?:\/confirm)?$/.test(trimmed)) {
    const base = trimmed.replace(/\/api\/chat(?:\/confirm)?$/, '')
    return `${base}/api/v1/recommend/task/${encodedTaskId}`
  }

  if (/\/api\/v1\/recommend(?:\/confirm)?$/.test(trimmed)) {
    const base = trimmed.replace(/\/api\/v1\/recommend(?:\/confirm)?$/, '')
    return `${base}/api/v1/recommend/task/${encodedTaskId}`
  }

  if (trimmed.endsWith('/api/v1/recommend/task')) {
    return `${trimmed}/${encodedTaskId}`
  }

  return `${trimmed.replace(/\/api\/chat\/confirm$/, '').replace(/\/api\/chat$/, '')}/api/v1/recommend/task/${encodedTaskId}`
}

export async function getRecommendTaskState (
  chatUrl : string,
  taskId : string
) : Promise<RecommendTaskStateResponse> {
  const response = await fetch(resolveTaskStateUrl(chatUrl, taskId), {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json'
    }
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return await response.json() as RecommendTaskStateResponse
}

export interface SessionStatePollOptions {
  intervalMs ?: number,
  timeoutMs ?: number,
  shouldStop ?: () => boolean,
  signal ?: AbortSignal
}

export interface SessionStatePollResult {
  status : 'resolved' | 'timeout' | 'stopped',
  state ?: SessionStateResponse
}

function sleep (ms : number, signal ?: AbortSignal) : Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve()
      return
    }

    const onAbort = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function pollSessionState (
  chatUrl : string,
  sessionId : string,
  onState : (state : SessionStateResponse) => boolean|Promise<boolean>,
  options : SessionStatePollOptions = {}
) : Promise<SessionStatePollResult> {
  const intervalMs = options.intervalMs ?? 2000
  const timeoutMs = options.timeoutMs ?? 90_000
  const startedAt = Date.now()

  while (Date.now() - startedAt <= timeoutMs) {
    if (options.signal?.aborted) {
      return { status: 'stopped' }
    }

    if (options.shouldStop?.()) {
      return { status: 'stopped' }
    }

    try {
      const state = await getSessionState(chatUrl, sessionId, options.signal)
      const shouldStopPolling = await onState(state)
      if (shouldStopPolling) {
        return {
          status: 'resolved',
          state
        }
      }
    }
    catch (error) {
      if (options.signal?.aborted) {
        return { status: 'stopped' }
      }
      console.warn('session-state-poll-failed', {
        session_id: sessionId,
        error
      })
    }

    if (Date.now() - startedAt >= timeoutMs) {
      break
    }

    await sleep(intervalMs, options.signal)
  }

  return { status: 'timeout' }
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

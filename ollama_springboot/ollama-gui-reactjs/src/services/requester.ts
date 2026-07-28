import {
  ChatStreamEvent,
  ConversationCreateRequest,
  ConversationCreateResponse,
  ImageAttachment,
  Message,
  RecommendTaskConfirmRequest,
  RecommendTaskCreateRequest,
  RecommendTaskStateResponse,
  RetrievedImage
} from '~/entities/messages'

function trimUrl (value : string) {
  return value.trim().replace(/\/+$/, '')
}

export function resolveApiBase (modelUrl : string) : string {
  const trimmed = trimUrl(modelUrl)

  if (/\/api\/chat(?:\/(?:confirm(?:\/stream)?|stream))?$/i.test(trimmed)) {
    return trimmed.replace(/\/api\/chat(?:\/(?:confirm(?:\/stream)?|stream))?$/i, '')
  }

  if (/\/api\/v1\/recommend(?:\/(?:confirm(?:\/stream)?|stream))?$/i.test(trimmed)) {
    return trimmed.replace(/\/api\/v1\/recommend(?:\/(?:confirm(?:\/stream)?|stream))?$/i, '')
  }

  return trimmed
}

function resolveConversationsUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/v1/conversations`
}

function resolveRecommendUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/v1/recommend`
}

function resolveChatStreamUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/chat/stream`
}

function resolveChatConfirmStreamUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/chat/confirm/stream`
}

function resolveRecommendStreamUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/v1/recommend/stream`
}

function resolveConfirmUrlByTask (
  modelUrl : string,
  taskId : string,
  action : 'confirm' | 'edit'
) {
  const encodedTaskId = encodeURIComponent(taskId)
  const encodedAction = encodeURIComponent(action)
  return `${resolveApiBase(modelUrl)}/api/v1/recommend/confirm?taskId=${encodedTaskId}&action=${encodedAction}`
}

function resolveConfirmStreamUrlByTask (
  modelUrl : string,
  taskId : string,
  action : 'confirm' | 'edit'
) {
  const encodedTaskId = encodeURIComponent(taskId)
  const encodedAction = encodeURIComponent(action)
  return `${resolveApiBase(modelUrl)}/api/v1/recommend/confirm/stream?taskId=${encodedTaskId}&action=${encodedAction}`
}

async function requestJson<T> (
  url : string,
  init : RequestInit = {}
) : Promise<T> {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {})
    },
    ...init
  })

  if (!response.ok) {
    const rawBody = await response.text()
    const bodyPreview = rawBody.slice(0, 300)
    throw new Error(`HTTP ${response.status}: ${bodyPreview}`)
  }

  return await response.json() as T
}

export async function createConversationRequester (
  modelUrl : string,
  payload : ConversationCreateRequest = {},
  signal ?: AbortSignal
) : Promise<ConversationCreateResponse> {
  return await requestJson<ConversationCreateResponse>(resolveConversationsUrl(modelUrl), {
    method: 'POST',
    body: JSON.stringify(payload),
    signal
  })
}

export async function createRecommendTaskRequester (
  modelUrl : string,
  payload : RecommendTaskCreateRequest,
  signal ?: AbortSignal
) : Promise<RecommendTaskStateResponse> {
  return await requestJson<RecommendTaskStateResponse>(resolveRecommendUrl(modelUrl), {
    method: 'POST',
    body: JSON.stringify(payload),
    signal
  })
}

interface ChatStreamRequestMessage {
  role : Message['role'],
  content : string
}

export interface ChatStreamRequest {
  model ?: string,
  messages : ChatStreamRequestMessage[],
  stream ?: boolean,
  conversation_id ?: string,
  session_id ?: string,
  images ?: ImageAttachment[]
}

export interface ChatConfirmStreamRequest {
  model ?: string,
  task_id ?: string,
  conversation_id ?: string,
  session_id ?: string,
  action ?: 'confirm' | 'edit',
  sub_questions ?: string[],
  comment ?: string
}

function isRecord (value : unknown) : value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function asTrimmedText (value : unknown) : string|undefined {
  if (value == null) return undefined
  const normalized = String(value).trim()
  return normalized ? normalized : undefined
}

function asRawText (value : unknown, fallback = '') : string {
  if (value == null) return fallback
  return typeof value === 'string' ? value : String(value)
}

function normalizeStringArray (raw : unknown) : string[] {
  if (raw == null) return []
  if (Array.isArray(raw)) {
    return raw
      .map((item) => asTrimmedText(item))
      .filter((item): item is string => Boolean(item))
  }
  const text = asTrimmedText(raw)
  return text ? [text] : []
}

function normalizeRetrievedImages (raw : unknown) : RetrievedImage[] {
  if (!Array.isArray(raw)) return []

  const normalized : RetrievedImage[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const docId = asTrimmedText(item.doc_id ?? item.docId)
    const url = asTrimmedText(item.url)
    if (!docId || !url || (!url.startsWith('/') && !/^https?:\/\//i.test(url))) {
      continue
    }
    const rawScore = Number(item.score)
    normalized.push({
      doc_id: docId,
      filename: asTrimmedText(item.filename) || docId,
      media_type: asTrimmedText(item.media_type ?? item.mediaType) || '',
      url,
      caption: asRawText(item.caption, ''),
      score: Number.isFinite(rawScore) ? rawScore : 0
    })
  }
  return normalized
}

const STREAM_EVENT_TYPES = [
  'meta',
  'node',
  'token',
  'state',
  'awaiting_confirmation',
  'final',
  'error'
] as const

function isStreamEventType (value : string) : value is ChatStreamEvent['type'] {
  return (STREAM_EVENT_TYPES as readonly string[]).includes(value)
}

function normalizeEventType (
  payload : Record<string, unknown>,
  eventName : string
) : ChatStreamEvent['type'] {
  const rawType = (asTrimmedText(payload.type) || asTrimmedText(eventName) || 'state').toLowerCase()
  return isStreamEventType(rawType) ? rawType : 'state'
}

function normalizeStreamEvent (
  payload : Record<string, unknown>,
  eventName : string
) : ChatStreamEvent {
  const type = normalizeEventType(payload, eventName)

  const taskId = asTrimmedText(payload.task_id ?? payload.taskId)
  const conversationId = asTrimmedText(payload.conversation_id ?? payload.conversationId)
  const sessionId = asTrimmedText(payload.session_id ?? payload.sessionId)

  if (type === 'meta') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      status: asTrimmedText(payload.status) || 'GENERATING'
    }
  }

  if (type === 'node') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      node: asTrimmedText(payload.node) || 'unknown',
      status: asTrimmedText(payload.status) || 'end',
      payload_keys: normalizeStringArray(payload.payload_keys ?? payload.payloadKeys)
    }
  }

  if (type === 'token') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      delta: asRawText(payload.delta ?? payload.message, ''),
      node: asTrimmedText(payload.node) || ''
    }
  }

  if (type === 'awaiting_confirmation') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      sub_questions: normalizeStringArray(
        payload.sub_questions
        ?? payload.pending_sub_questions
        ?? payload.subQuestions
        ?? payload.pendingSubQuestions
      )
    }
  }

  if (type === 'final') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      status: asTrimmedText(payload.status) || 'success',
      final_answer: asRawText(payload.final_answer ?? payload.finalResult ?? payload.message, ''),
      retrieved_doc_ids: normalizeStringArray(payload.retrieved_doc_ids ?? payload.retrievedDocIds),
      retrieved_images: normalizeRetrievedImages(payload.retrieved_images ?? payload.retrievedImages)
    }
  }

  if (type === 'error') {
    return {
      type,
      task_id: taskId,
      conversation_id: conversationId,
      session_id: sessionId,
      message: asRawText(payload.message, 'stream failed')
    }
  }

  return {
    type: 'state',
    task_id: taskId,
    conversation_id: conversationId,
    session_id: sessionId,
    payload: isRecord(payload.payload) ? payload.payload : payload
  }
}

function parseSseFrame (frame : string) : ChatStreamEvent|undefined {
  const trimmed = frame.trim()
  if (!trimmed) return undefined

  let eventName = 'message'
  const dataLines : string[] = []

  for (const line of trimmed.split('\n')) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || eventName
      continue
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  const dataText = dataLines.join('\n').trim()
  if (!dataText) return undefined

  try {
    const parsed = JSON.parse(dataText)
    if (isRecord(parsed)) {
      return normalizeStreamEvent(parsed, eventName)
    }
    return normalizeStreamEvent({ value: parsed }, eventName)
  }
  catch {
    if (eventName.trim().toLowerCase() === 'error') {
      return normalizeStreamEvent(
        {
          type: 'error',
          message: dataText
        },
        eventName
      )
    }
    return undefined
  }
}

async function streamSseRequester (
  url : string,
  payload : unknown,
  onEvent : (event : ChatStreamEvent) => void,
  signal ?: AbortSignal
) : Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream'
    },
    body: JSON.stringify(payload),
    signal
  })

  if (!response.ok) {
    const rawBody = await response.text()
    const bodyPreview = rawBody.slice(0, 300)
    throw new Error(`HTTP ${response.status}: ${bodyPreview}`)
  }

  if (!response.body) {
    throw new Error('Streaming response body is empty')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r/g, '')

    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const parsed = parseSseFrame(frame)
      if (parsed) {
        onEvent(parsed)
      }
      boundary = buffer.indexOf('\n\n')
    }
  }

  const tail = decoder.decode()
  if (tail) {
    buffer += tail
  }
  buffer = buffer.replace(/\r/g, '')
  const parsedTail = parseSseFrame(buffer)
  if (parsedTail) {
    onEvent(parsedTail)
  }
}

export async function streamRecommendRequester (
  modelUrl : string,
  payload : RecommendTaskCreateRequest,
  onEvent : (event : ChatStreamEvent) => void,
  signal ?: AbortSignal
) : Promise<void> {
  return await streamSseRequester(resolveRecommendStreamUrl(modelUrl), payload, onEvent, signal)
}

export async function streamChatRequester (
  modelUrl : string,
  payload : ChatStreamRequest,
  onEvent : (event : ChatStreamEvent) => void,
  signal ?: AbortSignal
) : Promise<void> {
  return await streamSseRequester(resolveChatStreamUrl(modelUrl), payload, onEvent, signal)
}

export async function streamChatConfirmRequester (
  modelUrl : string,
  payload : ChatConfirmStreamRequest,
  onEvent : (event : ChatStreamEvent) => void,
  signal ?: AbortSignal
) : Promise<void> {
  return await streamSseRequester(resolveChatConfirmStreamUrl(modelUrl), payload, onEvent, signal)
}

export async function streamConfirmRequester (
  modelUrl : string,
  taskId : string,
  action : 'confirm' | 'edit' = 'confirm',
  payload : RecommendTaskConfirmRequest = {},
  onEvent : (event : ChatStreamEvent) => void,
  signal ?: AbortSignal
) : Promise<void> {
  return await streamSseRequester(resolveConfirmStreamUrlByTask(modelUrl, taskId, action), payload, onEvent, signal)
}

export async function confirmTaskRequester (
  modelUrl : string,
  taskId : string,
  action : 'confirm' | 'edit' = 'confirm',
  payload : RecommendTaskConfirmRequest = {},
  signal ?: AbortSignal
) : Promise<RecommendTaskStateResponse> {
  return await requestJson<RecommendTaskStateResponse>(resolveConfirmUrlByTask(modelUrl, taskId, action), {
    method: 'POST',
    body: JSON.stringify(payload),
    signal
  })
}

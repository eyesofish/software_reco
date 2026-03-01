import {
  ConversationCreateRequest,
  ConversationCreateResponse,
  RecommendTaskConfirmRequest,
  RecommendTaskCreateRequest,
  RecommendTaskStateResponse
} from '~/entities/messages'

function trimUrl (value : string) {
  return value.trim().replace(/\/+$/, '')
}

export function resolveApiBase (modelUrl : string) : string {
  const trimmed = trimUrl(modelUrl)

  if (/\/api\/chat(?:\/confirm)?$/i.test(trimmed)) {
    return trimmed.replace(/\/api\/chat(?:\/confirm)?$/i, '')
  }

  if (/\/api\/v1\/recommend(?:\/confirm)?$/i.test(trimmed)) {
    return trimmed.replace(/\/api\/v1\/recommend(?:\/confirm)?$/i, '')
  }

  return trimmed
}

function resolveConversationsUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/v1/conversations`
}

function resolveRecommendUrl (modelUrl : string) {
  return `${resolveApiBase(modelUrl)}/api/v1/recommend`
}

function resolveTaskUrl (modelUrl : string, taskId : string) {
  const encoded = encodeURIComponent(taskId)
  return `${resolveApiBase(modelUrl)}/api/v1/tasks/${encoded}`
}

function resolveConversationTasksUrl (modelUrl : string, conversationId : string) {
  const encoded = encodeURIComponent(conversationId)
  return `${resolveApiBase(modelUrl)}/api/v1/conversations/${encoded}/tasks`
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

export async function getTaskStateRequester (
  modelUrl : string,
  taskId : string,
  signal ?: AbortSignal
) : Promise<RecommendTaskStateResponse> {
  return await requestJson<RecommendTaskStateResponse>(resolveTaskUrl(modelUrl, taskId), {
    method: 'GET',
    signal
  })
}

export async function listConversationTasksRequester (
  modelUrl : string,
  conversationId : string,
  signal ?: AbortSignal
) : Promise<RecommendTaskStateResponse[]> {
  return await requestJson<RecommendTaskStateResponse[]>(resolveConversationTasksUrl(modelUrl, conversationId), {
    method: 'GET',
    signal
  })
}

export const getRecommendTaskState = getTaskStateRequester
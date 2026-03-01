import { RefObject, useEffect, useReducer, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import {
  CONFIRM_PENDING_STORAGE_PREFIX,
  CONFIRM_RETRY_INTERVAL_TICKS,
  CONFIRM_STATUS_POLL_INTERVAL_MS,
  CONFIRM_STATUS_POLL_TIMEOUT_MS,
  CONFIRM_GUIDE_MESSAGE,
  CONFIRM_ONLY_PATTERN,
  PENDING_LINE_PATTERN,
  RECOVERY_POLL_INTERVAL_MS,
  RECOVERY_POLL_TIMEOUT_MS,
  REQUEST_FAILED_MESSAGE,
  TASK_ID_STORAGE_PREFIX,
  WAITING_ACK_PATTERN
} from '~/constants/chat'
import { ConfirmPayload, Message, ModelResponse, SessionStateMessage, SessionStateResponse } from '~/entities/messages'
import createAssistantMessage from '~/services/createAssistantMessage'
import createUserMessage from '~/services/createUserMessage'
import getChatIndex from '~/services/getChatIndex'
import requester, { confirmRequester, getRecommendTaskState, getSessionState, pollSessionState, resolveConfirmUrl } from '~/services/requester'
import scroller from '~/services/scroller'
import Store from '~/services/store'
import { disChats, useChats } from '~/stores/chats'
import { addMessage, loadChats } from '~/stores/chats/actions'
import { useConfig } from '~/stores/config'

export interface ChatState {
  currentChatId : number|null,
  loading : boolean,
  isAwaitingConfirmation : boolean,
  pendingSubQuestions : string[],
  pendingSessionId : string|undefined,
  confirmLoading : boolean
}

type ChatAction =
  | { type : 'SET_CURRENT_CHAT_ID', payload : number|null }
  | { type : 'SET_LOADING', payload : boolean }
  | { type : 'SET_CONFIRM_LOADING', payload : boolean }
  | {
    type : 'SET_CONFIRMATION_STATE',
    payload : {
      isAwaitingConfirmation : boolean,
      pendingSubQuestions : string[],
      pendingSessionId : string|undefined
    }
  }
  | { type : 'RESET_CONFIRMATION_STATE' }

const INITIAL_CHAT_STATE : ChatState = {
  currentChatId: null,
  loading: true,
  isAwaitingConfirmation: false,
  pendingSubQuestions: [],
  pendingSessionId: undefined,
  confirmLoading: false
}

function chatStateReducer (state : ChatState, action : ChatAction) : ChatState {
  switch (action.type) {
    case 'SET_CURRENT_CHAT_ID':
      return { ...state, currentChatId: action.payload }
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'SET_CONFIRM_LOADING':
      return { ...state, confirmLoading: action.payload }
    case 'SET_CONFIRMATION_STATE':
      return {
        ...state,
        isAwaitingConfirmation: action.payload.isAwaitingConfirmation,
        pendingSubQuestions: action.payload.pendingSubQuestions,
        pendingSessionId: action.payload.pendingSessionId
      }
    case 'RESET_CONFIRMATION_STATE':
      return {
        ...state,
        isAwaitingConfirmation: false,
        pendingSubQuestions: [],
        pendingSessionId: undefined
      }
    default:
      return state
  }
}

interface UseChatLogicResult {
  rowContainerRef : RefObject<HTMLDivElement>,
  textAreaRef : RefObject<HTMLTextAreaElement>,
  messages : Message[],
  hasTalk : boolean,
  loading : boolean,
  confirmLoading : boolean,
  isAwaitingConfirmation : boolean,
  pendingSubQuestions : string[],
  pendingSessionId : string|undefined,
  handleDeleteChat : (chatId : number) => void,
  requestHandler : () => void,
  handleConfirmClicked : () => Promise<void>
}

interface ConfirmPendingState {
  sessionId : string,
  conversationId ?: string,
  pendingSubQuestions : string[],
  updatedAt : number
}

export default function useChatLogic () : UseChatLogicResult {
  const navigate = useNavigate()
  const { chat } = useParams()
  const [chatState, dispatch] = useReducer(chatStateReducer, INITIAL_CHAT_STATE)
  const chats = useChats('chats')
  const { autoSaveChats, modelName, modelUrl } = useConfig('config')
  const activeConversationId = useRef<string|undefined>(undefined)
  const activeSessionId = useRef<string|undefined>(undefined)
  const shouldRestoreHistoryOnEnter = useRef(true)
  const renderCount = useRef(0)
  const rowContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const chatsRef = useRef(chats)
  const currentChatIdRef = useRef<number|null>(null)
  const recoveryAttemptRef = useRef(0)
  const recoveryAbortControllerRef = useRef<AbortController|null>(null)
  const confirmPollAttemptRef = useRef(0)
  const confirmPollAbortControllerRef = useRef<AbortController|null>(null)
  const confirmPollTargetRef = useRef<string|undefined>(undefined)
  const {
    currentChatId,
    loading,
    isAwaitingConfirmation,
    pendingSubQuestions,
    pendingSessionId,
    confirmLoading
  } = chatState

  function setCurrentChatId (chatId : number|null) {
    dispatch({ type: 'SET_CURRENT_CHAT_ID', payload: chatId })
  }

  function setLoading (nextLoading : boolean) {
    dispatch({ type: 'SET_LOADING', payload: nextLoading })
  }

  function setConfirmLoading (nextLoading : boolean) {
    dispatch({ type: 'SET_CONFIRM_LOADING', payload: nextLoading })
  }

  function setConfirmationState (
    awaiting : boolean,
    pending : string[],
    sessionId ?: string
  ) {
    dispatch({
      type: 'SET_CONFIRMATION_STATE',
      payload: {
        isAwaitingConfirmation: awaiting,
        pendingSubQuestions: pending,
        pendingSessionId: sessionId
      }
    })
  }

  function resetConfirmationState () {
    dispatch({ type: 'RESET_CONFIRMATION_STATE' })
  }

  function resolveConversationRefs (messages : Message[]|undefined) : {
    conversationId ?: string,
    sessionId ?: string
  } {
    if (!messages || messages.length === 0) {
      return {}
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      const message = messages[i]
      const conversationId = message.conversationId?.trim()
      const sessionId = message.sessionId?.trim()
      if (conversationId || sessionId) {
        return { conversationId, sessionId }
      }
    }
    return {}
  }

  function isConfirmationOnlyText (text : string) : boolean {
    return CONFIRM_ONLY_PATTERN.test(text.trim())
  }

  function generateSessionId () : string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`
  }

  function taskStorageKey (chatId : number) : string {
    return `${TASK_ID_STORAGE_PREFIX}${chatId}`
  }

  function readStoredTaskId (chatId : number|null) : string|undefined {
    if (chatId === null) return undefined
    try {
      const candidate = localStorage.getItem(taskStorageKey(chatId))?.trim()
      return candidate || undefined
    } catch {
      return undefined
    }
  }

  function storeTaskId (chatId : number|null, taskId ?: string) {
    if (chatId === null) return
    const value = String(taskId || '').trim()
    try {
      if (!value) {
        localStorage.removeItem(taskStorageKey(chatId))
        return
      }
      localStorage.setItem(taskStorageKey(chatId), value)
    } catch {
      // localStorage unavailable
    }
  }

  function confirmPendingStorageKey (chatId : number) : string {
    return `${CONFIRM_PENDING_STORAGE_PREFIX}${chatId}`
  }

  function readConfirmPendingState (chatId : number|null) : ConfirmPendingState|undefined {
    if (chatId === null) return undefined
    try {
      const raw = localStorage.getItem(confirmPendingStorageKey(chatId))
      if (!raw) return undefined
      const parsed = JSON.parse(raw) as Partial<ConfirmPendingState>
      const sessionId = String(parsed.sessionId || '').trim()
      if (!sessionId) return undefined
      return {
        sessionId,
        conversationId: String(parsed.conversationId || '').trim() || undefined,
        pendingSubQuestions: normalizePendingSubQuestions(parsed.pendingSubQuestions),
        updatedAt: Number(parsed.updatedAt || Date.now())
      }
    } catch {
      return undefined
    }
  }

  function storeConfirmPendingState (
    chatId : number|null,
    sessionId : string,
    conversationId ?: string,
    pending : string[] = []
  ) {
    if (chatId === null) return
    const normalizedSessionId = String(sessionId || '').trim()
    if (!normalizedSessionId) return
    try {
      const payload : ConfirmPendingState = {
        sessionId: normalizedSessionId,
        conversationId: String(conversationId || '').trim() || undefined,
        pendingSubQuestions: normalizePendingSubQuestions(pending),
        updatedAt: Date.now()
      }
      localStorage.setItem(confirmPendingStorageKey(chatId), JSON.stringify(payload))
    } catch {
      // localStorage unavailable
    }
  }

  function clearConfirmPendingState (chatId : number|null) {
    if (chatId === null) return
    try {
      localStorage.removeItem(confirmPendingStorageKey(chatId))
    } catch {
      // localStorage unavailable
    }
  }

  function normalizeTaskStatus (status : unknown) : string {
    return String(status || '').trim().toUpperCase()
  }

  function isTerminalTaskStatus (status : string, finalResult : string) : boolean {
    if (finalResult.trim()) return true
    return status === 'DONE' || status === 'FAILED'
  }

  async function sleepWithAbort (ms : number, signal ?: AbortSignal) : Promise<void> {
    await new Promise<void>((resolve) => {
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

  function buildMessageKey (role : Message['role'], content : string) : string {
    return `${role}::${content.trim()}`
  }

  function buildMessageOccurrences (
    messages : Array<Pick<Message, 'role' | 'content'>>
  ) : Map<string, number> {
    const occurrences = new Map<string, number>()
    for (const message of messages) {
      const key = buildMessageKey(message.role, message.content)
      occurrences.set(key, (occurrences.get(key) || 0) + 1)
    }
    return occurrences
  }

  function mergeRestoredWithLocalMessages (
    localMessages : Message[],
    restoredMessages : Message[]
  ) : Message[] {
    if (localMessages.length === 0) return restoredMessages
    if (restoredMessages.length === 0) return localMessages

    const restoredOccurrences = buildMessageOccurrences(restoredMessages)
    const localOnlyMessages : Message[] = []

    for (const message of localMessages) {
      const key = buildMessageKey(message.role, message.content)
      const remaining = restoredOccurrences.get(key) || 0
      if (remaining > 0) {
        restoredOccurrences.set(key, remaining - 1)
        continue
      }
      localOnlyMessages.push(message)
    }

    return [...restoredMessages, ...localOnlyMessages]
  }

  function cancelRecoveryPolling () {
    recoveryAttemptRef.current += 1
    recoveryAbortControllerRef.current?.abort()
    recoveryAbortControllerRef.current = null
  }

  function cancelConfirmStatusPolling () {
    confirmPollAttemptRef.current += 1
    confirmPollAbortControllerRef.current?.abort()
    confirmPollAbortControllerRef.current = null
    confirmPollTargetRef.current = undefined
  }

  function isImmediateDuplicateMessage (
    chatId : number,
    role : Message['role'],
    content : string
  ) : boolean {
    const currentMessages = chatsRef.current[chatId] || []
    if (currentMessages.length === 0) return false
    const lastMessage = currentMessages[currentMessages.length - 1]
    return buildMessageKey(lastMessage.role, lastMessage.content) === buildMessageKey(role, content)
  }

  function hasAnyDuplicateMessage (
    chatId : number,
    role : Message['role'],
    content : string
  ) : boolean {
    const currentMessages = chatsRef.current[chatId] || []
    if (currentMessages.length === 0) return false
    const candidateKey = buildMessageKey(role, content)
    for (const message of currentMessages) {
      if (buildMessageKey(message.role, message.content) === candidateKey) {
        return true
      }
    }
    return false
  }

  function handleDeleteChat (chatId : number) {
    storeTaskId(chatId, undefined)
    clearConfirmPendingState(chatId)
    if (currentChatId !== chatId) return
    cancelRecoveryPolling()
    cancelConfirmStatusPolling()
    setCurrentChatId(null)
    shouldRestoreHistoryOnEnter.current = false
    activeConversationId.current = undefined
    activeSessionId.current = undefined
    resetConfirmationState()
    setConfirmLoading(false)
    setLoading(false)
  }

  function appendAssistantMessage (content : string, conversationId ?: string, sessionId ?: string) : boolean {
    if (currentChatIdRef.current === null) return false
    const rawContent = String(content || '')
    if (!rawContent.trim()) return false
    const chatId = currentChatIdRef.current
    if (isImmediateDuplicateMessage(chatId, 'assistant', rawContent)) {
      return false
    }
    const assistantMessage = createAssistantMessage(rawContent, conversationId, sessionId)
    disChats(addMessage({ index: chatId, message: assistantMessage }))
    return true
  }

  function normalizeSessionMessages (messages : SessionStateMessage[]|undefined) : SessionStateMessage[] {
    if (!Array.isArray(messages)) {
      return []
    }

    const normalized : SessionStateMessage[] = []
    for (const item of messages as Array<Partial<SessionStateMessage>>) {
      const role = String(item.role || '').trim().toLowerCase()
      const content = String(item.content || '')
      if (!content.trim()) continue
      if (role !== 'user' && role !== 'assistant' && role !== 'system') continue
      normalized.push({ role: role as SessionStateMessage['role'], content })
    }
    return normalized
  }

  function appendRecoveredSessionMessages (
    chatId : number,
    messages : SessionStateMessage[]|undefined,
    conversationId : string,
    sessionId : string
  ) : number {
    const normalized = normalizeSessionMessages(messages)
    if (normalized.length === 0) {
      return 0
    }

    const currentMessages = chatsRef.current[chatId] || []
    const existingOccurrences = buildMessageOccurrences(currentMessages)
    const sourceSeenOccurrences = new Map<string, number>()
    const recoveredMessages : Message[] = []
    let nextTime = Date.now()

    for (const message of normalized) {
      if (message.role !== 'assistant' && message.role !== 'system') {
        continue
      }
      const messageKey = buildMessageKey(message.role, message.content)
      const nextSeen = (sourceSeenOccurrences.get(messageKey) || 0) + 1
      sourceSeenOccurrences.set(messageKey, nextSeen)

      const existingCount = existingOccurrences.get(messageKey) || 0
      if (nextSeen <= existingCount) {
        continue
      }
      existingOccurrences.set(messageKey, existingCount + 1)
      recoveredMessages.push({
        role: message.role,
        content: message.content,
        time: nextTime++,
        conversationId,
        sessionId
      })
    }

    if (recoveredMessages.length === 0) {
      return 0
    }

    const nextChats = [...chatsRef.current]
    const prevMessages = Array.isArray(nextChats[chatId]) ? [...nextChats[chatId]] : []
    nextChats[chatId] = [...prevMessages, ...recoveredMessages]
    disChats(loadChats(nextChats))

    return recoveredMessages.length
  }

  function normalizePendingSubQuestions (raw : unknown) : string[] {
    if (!Array.isArray(raw)) {
      return []
    }
    const normalized : string[] = []
    for (const item of raw) {
      const text = String(item || '').trim()
      if (!text) continue
      normalized.push(text)
    }
    return normalized
  }

  function parsePendingSubQuestionsFromText (content : string) : string[] {
    const pending : string[] = []
    const lines = content.split(/\r?\n/)
    for (const line of lines) {
      const matched = line.match(PENDING_LINE_PATTERN)
      if (!matched) continue
      const question = String(matched[1] || '').trim()
      if (!question) continue
      pending.push(question)
    }
    return pending
  }

  function resolveHitlStateFromSessionState (sessionState : SessionStateResponse) : {
    awaiting : boolean,
    pending : string[]
  } {
    const structuredPending = normalizePendingSubQuestions(
      sessionState.pending_sub_questions ?? sessionState.pendingSubQuestions
    )
    const structuredAwaiting = sessionState.awaiting_human_confirmation ?? sessionState.awaitingHumanConfirmation

    if (typeof structuredAwaiting === 'boolean') {
      return {
        awaiting: structuredAwaiting,
        pending: structuredPending
      }
    }
    if (structuredPending.length > 0) {
      return {
        awaiting: true,
        pending: structuredPending
      }
    }

    const restoredSessionMessages = normalizeSessionMessages(sessionState.messages)
    for (let i = restoredSessionMessages.length - 1; i >= 0; i--) {
      const message = restoredSessionMessages[i]
      if (message.role !== 'assistant') continue
      const content = String(message.content || '').trim()
      if (!content) continue
      const parsedPending = parsePendingSubQuestionsFromText(content)
      const awaiting = WAITING_ACK_PATTERN.test(content) || parsedPending.length > 0
      return {
        awaiting,
        pending: parsedPending
      }
    }

    return {
      awaiting: false,
      pending: []
    }
  }

  function applyRecoveredHitlState (
    sessionState : SessionStateResponse,
    sessionId : string
  ) {
    const hitl = resolveHitlStateFromSessionState(sessionState)
    if (hitl.awaiting) {
      setConfirmationState(true, hitl.pending, sessionId)
      return
    }

    resetConfirmationState()
  }

  async function restoreMessagesFromSessionState (
    chatId : number,
    sessionId : string,
    conversationId ?: string
  ) {
    try {
      const sessionState = await getSessionState(modelUrl, sessionId)
      const restoredSessionMessages = normalizeSessionMessages(sessionState.messages)
      const resolvedConversationId = conversationId || sessionState.conversation_id || sessionId

      if (restoredSessionMessages.length > 0) {
        const baseTime = Date.now()
        const restoredMessages : Message[] = restoredSessionMessages.map((message, index) => ({
          role: message.role,
          content: message.content,
          time: baseTime + index,
          conversationId: resolvedConversationId,
          sessionId
        }))

        const nextChats = [...chatsRef.current]
        const localMessages = Array.isArray(nextChats[chatId]) ? [...nextChats[chatId]] : []
        nextChats[chatId] = mergeRestoredWithLocalMessages(localMessages, restoredMessages)
        disChats(loadChats(nextChats))
      }

      activeConversationId.current = resolvedConversationId
      activeSessionId.current = sessionId
      applyRecoveredHitlState(sessionState, sessionId)

      console.info('session-state-restored', {
        session_id: sessionId,
        restored_count: restoredSessionMessages.length
      })
    } catch (error) {
      console.warn('session-state-restore-failed', {
        session_id: sessionId,
        error
      })
    }
  }

  async function restoreStateFromTaskState (
    chatId : number,
    taskId : string,
    conversationId ?: string
  ) : Promise<{
    status : string,
    awaiting : boolean,
    pending : string[],
    finalResult : string
  }> {
    try {
      const taskState = await getRecommendTaskState(modelUrl, taskId)
      const status = normalizeTaskStatus(taskState.status)
      const finalResult = String(taskState.final_result ?? taskState.finalResult ?? '').trim()
      const subQuestions = normalizePendingSubQuestions(
        taskState.sub_questions ?? taskState.subQuestions
      )
      const resolvedConversationId = conversationId || activeConversationId.current || taskId

      if (finalResult && !hasAnyDuplicateMessage(chatId, 'assistant', finalResult)) {
        appendAssistantMessage(finalResult, resolvedConversationId, taskId)
      }

      if (status === 'PENDING_CONFIRM') {
        setConfirmationState(true, subQuestions, taskId)
      } else {
        resetConfirmationState()
      }

      activeConversationId.current = resolvedConversationId
      activeSessionId.current = taskId
      storeTaskId(chatId, taskId)

      console.info('task-state-restored', {
        task_id: taskId,
        status,
        has_final_result: finalResult.length > 0,
        pending_count: subQuestions.length
      })

      return {
        status,
        awaiting: status === 'PENDING_CONFIRM',
        pending: subQuestions,
        finalResult
      }
    } catch (error) {
      console.warn('task-state-restore-failed', {
        task_id: taskId,
        error
      })
      return {
        status: '',
        awaiting: false,
        pending: [],
        finalResult: ''
      }
    }
  }

  function applyConfirmResponseState (
    chatId : number,
    fallbackSessionId : string,
    response : ModelResponse
  ) : {
    sessionId : string,
    conversationId : string,
    awaiting : boolean,
    pending : string[]
  } {
    const conversationId = String(
      response.conversation_id || activeConversationId.current || fallbackSessionId
    )
    const sessionId = String(
      response.session_id || activeSessionId.current || fallbackSessionId
    )
    const content = String(response.message?.content || response.final_answer || '')
    const awaiting = response.awaiting_human_confirmation === true
    const pending = normalizePendingSubQuestions(response.pending_sub_questions || [])

    activeConversationId.current = conversationId
    activeSessionId.current = sessionId
    storeTaskId(chatId, sessionId)

    if (content) {
      appendAssistantMessage(content, conversationId, sessionId)
    }

    if (awaiting) {
      setConfirmationState(true, pending, sessionId)
      storeConfirmPendingState(chatId, sessionId, conversationId, pending)
    } else {
      resetConfirmationState()
      clearConfirmPendingState(chatId)
    }

    return {
      sessionId,
      conversationId,
      awaiting,
      pending
    }
  }

  async function submitConfirmSilently (
    chatId : number,
    sessionId : string,
    subQuestions : string[] = []
  ) : Promise<{
    sessionId : string,
    conversationId : string,
    awaiting : boolean,
    pending : string[]
  }|undefined> {
    const confirmPayload : ConfirmPayload = {
      session_id: sessionId,
      action: 'confirm',
      sub_questions: subQuestions,
      comment: 'confirm'
    }

    try {
      const confirmResponse = await confirmRequester(
        resolveConfirmUrl(modelUrl),
        confirmPayload
      )
      return applyConfirmResponseState(chatId, sessionId, confirmResponse)
    } catch (error) {
      console.warn('confirm-submit-failed', {
        session_id: sessionId,
        error
      })
      return undefined
    }
  }

  function startConfirmStatusPolling (
    chatId : number,
    sessionId : string,
    conversationId ?: string,
    pendingFromState : string[] = []
  ) {
    const normalizedSessionId = String(sessionId || '').trim()
    if (!normalizedSessionId) return

    const targetKey = `${chatId}:${normalizedSessionId}`
    if (
      confirmPollTargetRef.current === targetKey
      && confirmPollAbortControllerRef.current
      && !confirmPollAbortControllerRef.current.signal.aborted
    ) {
      return
    }

    cancelConfirmStatusPolling()
    setConfirmLoading(true)
    setConfirmationState(true, normalizePendingSubQuestions(pendingFromState), normalizedSessionId)
    storeConfirmPendingState(chatId, normalizedSessionId, conversationId, pendingFromState)

    const localAttemptId = ++confirmPollAttemptRef.current
    const controller = new AbortController()
    confirmPollAbortControllerRef.current = controller
    confirmPollTargetRef.current = targetKey

    void (async () => {
      let retryTick = 0
      let startedAt = Date.now()

      while (
        !controller.signal.aborted
        && confirmPollAttemptRef.current === localAttemptId
        && currentChatIdRef.current === chatId
      ) {
        if (retryTick % CONFIRM_RETRY_INTERVAL_TICKS === 0) {
          const submitted = await submitConfirmSilently(chatId, normalizedSessionId)
          if (controller.signal.aborted) return
          if (submitted && !submitted.awaiting) {
            clearConfirmPendingState(chatId)
            setConfirmLoading(false)
            cancelRecoveryPolling()
            const recoveryController = new AbortController()
            recoveryAbortControllerRef.current = recoveryController
            void recoverMessagesByPolling(
              chatId,
              submitted.sessionId,
              submitted.conversationId,
              recoveryController.signal
            )
            return
          }
        }

        const taskState = await restoreStateFromTaskState(chatId, normalizedSessionId, conversationId)
        if (controller.signal.aborted) return

        if (taskState.awaiting) {
          setConfirmLoading(true)
          setConfirmationState(true, taskState.pending, normalizedSessionId)
          storeConfirmPendingState(chatId, normalizedSessionId, conversationId, taskState.pending)
        } else {
          clearConfirmPendingState(chatId)
          setConfirmLoading(false)
          if (!isTerminalTaskStatus(taskState.status, taskState.finalResult)) {
            cancelRecoveryPolling()
            const recoveryController = new AbortController()
            recoveryAbortControllerRef.current = recoveryController
            void recoverMessagesByPolling(
              chatId,
              normalizedSessionId,
              conversationId,
              recoveryController.signal
            )
          }
          return
        }

        retryTick++
        if (Date.now() - startedAt >= CONFIRM_STATUS_POLL_TIMEOUT_MS) {
          startedAt = Date.now()
          retryTick = 0
        }
        await sleepWithAbort(CONFIRM_STATUS_POLL_INTERVAL_MS, controller.signal)
      }
    })()
  }

  async function recoverMessagesByPolling (
    chatId : number,
    sessionId : string,
    conversationId ?: string,
    signal ?: AbortSignal
  ) {
    const localAttemptId = ++recoveryAttemptRef.current
    const resolvedConversationId = conversationId || sessionId

    try {
      const result = await pollSessionState(
        modelUrl,
        sessionId,
        (sessionState) => {
          if (currentChatIdRef.current !== chatId) {
            return true
          }
          const recoveredCount = appendRecoveredSessionMessages(
            chatId,
            sessionState.messages,
            resolvedConversationId,
            sessionId
          )
          const hitlState = resolveHitlStateFromSessionState(sessionState)
          applyRecoveredHitlState(sessionState, sessionId)

          if (recoveredCount > 0 || hitlState.awaiting) {
            activeConversationId.current = resolvedConversationId
            activeSessionId.current = sessionId
            console.info('session-state-recovered-after-failure', {
              session_id: sessionId,
              recovered_count: recoveredCount
            })
            return true
          }
          return false
        },
        {
          intervalMs: RECOVERY_POLL_INTERVAL_MS,
          timeoutMs: RECOVERY_POLL_TIMEOUT_MS,
          signal,
          shouldStop: () => (
            signal?.aborted === true
            || recoveryAttemptRef.current !== localAttemptId
            || currentChatIdRef.current !== chatId
          )
        }
      )

      if (!signal?.aborted && result.status === 'timeout') {
        alert(REQUEST_FAILED_MESSAGE)
      }
    } catch (error) {
      if (signal?.aborted) {
        return
      }
      alert(REQUEST_FAILED_MESSAGE)
      console.error(error)
    } finally {
      if (!signal?.aborted && recoveryAttemptRef.current === localAttemptId) {
        setLoading(false)
      }
    }
  }

  function requestHandler () {
    if (loading || confirmLoading || currentChatId === null) return
    const messageText = getMessageText().trim()
    if (!messageText) return

    if (isConfirmationOnlyText(messageText)) {
      clearTextArea()
      appendAssistantMessage(
        CONFIRM_GUIDE_MESSAGE,
        activeConversationId.current,
        activeSessionId.current
      )
      return
    }

    cancelRecoveryPolling()
    cancelConfirmStatusPolling()
    clearConfirmPendingState(currentChatId)
    setLoading(true)
    shouldRestoreHistoryOnEnter.current = false
    resetConfirmationState()

    const currentMessages = chats[currentChatId] || []
    const refs = resolveConversationRefs(currentMessages)
    const stableSessionId = refs.sessionId || refs.conversationId || generateSessionId()
    const stableConversationId = refs.conversationId || stableSessionId

    activeConversationId.current = stableConversationId
    activeSessionId.current = stableSessionId
    storeTaskId(currentChatId, stableSessionId)

    const userMessage = createUserMessage(
      messageText,
      stableConversationId,
      stableSessionId
    )
    const messagesForRequest = [...currentMessages, userMessage]

    clearTextArea()
    disChats(addMessage({ index: currentChatId, message: userMessage }))

    requester(
      modelUrl,
      modelName,
      messagesForRequest,
      stableConversationId,
      responseHandler,
      errorHandler
    )
  }

  function responseHandler (response : ModelResponse) {
    const conversationId = response.conversation_id || activeConversationId.current || response.session_id
    const sessionId = response.session_id || activeSessionId.current || conversationId
    const content = response.message?.content || response.final_answer || ''

    activeConversationId.current = conversationId
    activeSessionId.current = sessionId
    storeTaskId(currentChatIdRef.current, sessionId)

    if (content) {
      appendAssistantMessage(content, conversationId, sessionId)
    }

    if (response.awaiting_human_confirmation) {
      const pending = response.pending_sub_questions || []
      setConfirmationState(true, pending, sessionId)
      clearConfirmPendingState(currentChatIdRef.current)
      setConfirmLoading(false)
      console.info('recommend-awaiting', {
        session_id: sessionId,
        status: response.status,
        pending_count: pending.length
      })
    } else {
      resetConfirmationState()
      clearConfirmPendingState(currentChatIdRef.current)
      setConfirmLoading(false)
    }

    setLoading(false)
  }

  async function handleConfirmClicked () {
    if (confirmLoading || !pendingSessionId || currentChatId === null) return

    const sessionId = pendingSessionId
    const conversationId = activeConversationId.current
    const pending = [...pendingSubQuestions]

    console.info('confirm-clicked', {
      session_id: sessionId
    })

    setConfirmLoading(true)
    setConfirmationState(true, pending, sessionId)
    storeConfirmPendingState(currentChatId, sessionId, conversationId, pending)
    startConfirmStatusPolling(currentChatId, sessionId, conversationId, pending)
  }

  function errorHandler (error : unknown) {
    console.error(error)
    const sessionId = activeSessionId.current?.trim()
    const chatId = currentChatIdRef.current

    if (!sessionId || chatId === null) {
      alert(REQUEST_FAILED_MESSAGE)
      setLoading(false)
      return
    }

    console.warn('recommend-request-failed-start-recovery', {
      session_id: sessionId,
      error
    })

    cancelRecoveryPolling()
    const recoveryController = new AbortController()
    recoveryAbortControllerRef.current = recoveryController
    void recoverMessagesByPolling(chatId, sessionId, activeConversationId.current, recoveryController.signal)
  }

  function getMessageText () {
    return textAreaRef.current?.value || ''
  }

  function clearTextArea () {
    const textArea = textAreaRef.current
    if (!textArea) return
    textArea.value = ''
    textArea.focus()
  }

  useEffect(() => {
    chatsRef.current = chats
  }, [chats])

  useEffect(() => {
    currentChatIdRef.current = currentChatId
  }, [currentChatId])

  useEffect(() => {
    renderCount.current++
    if (!autoSaveChats) return
    if (renderCount.current < 3) return
    Store.set('chats', chats)
  }, [autoSaveChats, chats])

  useEffect(() => {
    if (currentChatId === null) {
      setLoading(false)
      return
    }
    if (currentChatId < 0 || currentChatId > chats.length) {
      navigate(ROUTES.ROOT)
      return
    }

    const currentMessages = chats[currentChatId]
    // The slot may briefly be undefined while hydration happens.
    // Explicitly end loading here to avoid getting stuck behind the spinner.
    if (!Array.isArray(currentMessages)) {
      setLoading(false)
      return
    }

    scroller(rowContainerRef, 1)
    const refs = resolveConversationRefs(currentMessages)
    const restoreSessionId = refs.sessionId || refs.conversationId
    const restoreConversationId = refs.conversationId || restoreSessionId
    const restoreTaskId = readStoredTaskId(currentChatId) || restoreSessionId
    activeConversationId.current = restoreConversationId
    activeSessionId.current = restoreSessionId
    storeTaskId(currentChatId, restoreTaskId)

    if (shouldRestoreHistoryOnEnter.current) {
      shouldRestoreHistoryOnEnter.current = false

      if (restoreSessionId || restoreTaskId) {
        setLoading(true)
        void (async () => {
          let restoredTask : {
            status : string,
            awaiting : boolean,
            pending : string[],
            finalResult : string
          }|undefined

          if (restoreSessionId) {
            await restoreMessagesFromSessionState(currentChatId, restoreSessionId, restoreConversationId)
          }
          if (restoreTaskId) {
            restoredTask = await restoreStateFromTaskState(currentChatId, restoreTaskId, restoreConversationId)
          }

          const persistedConfirm = readConfirmPendingState(currentChatId)
          const recoveredConfirmSessionId = persistedConfirm?.sessionId
            || (restoredTask?.awaiting ? restoreTaskId : undefined)

          if (recoveredConfirmSessionId) {
            const recoveredPending = persistedConfirm?.pendingSubQuestions?.length
              ? persistedConfirm.pendingSubQuestions
              : (restoredTask?.pending || [])
            startConfirmStatusPolling(
              currentChatId,
              recoveredConfirmSessionId,
              restoreConversationId,
              recoveredPending
            )
          } else {
            clearConfirmPendingState(currentChatId)
            setConfirmLoading(false)
          }
        })().finally(() => setLoading(false))
        return
      }

      clearConfirmPendingState(currentChatId)
      setConfirmLoading(false)
      setLoading(false)
      return
    }
  }, [chats, currentChatId, modelUrl, navigate])

  useEffect(() => {
    cancelRecoveryPolling()
    cancelConfirmStatusPolling()
    textAreaRef.current?.focus()
    shouldRestoreHistoryOnEnter.current = true
    setCurrentChatId(getChatIndex())
    activeConversationId.current = undefined
    activeSessionId.current = undefined
    resetConfirmationState()
    setConfirmLoading(false)
  }, [chat, modelUrl, navigate])

  useEffect(() => {
    return () => {
      recoveryAttemptRef.current += 1
      recoveryAbortControllerRef.current?.abort()
      recoveryAbortControllerRef.current = null
      confirmPollAttemptRef.current += 1
      confirmPollAbortControllerRef.current?.abort()
      confirmPollAbortControllerRef.current = null
      confirmPollTargetRef.current = undefined
    }
  }, [])

  const messages = currentChatId === null
    ? []
    : chats[currentChatId] || []
  const hasTalk = messages.length > 0

  return {
    rowContainerRef,
    textAreaRef,
    messages,
    hasTalk,
    loading,
    confirmLoading,
    isAwaitingConfirmation,
    pendingSubQuestions,
    pendingSessionId,
    handleDeleteChat,
    requestHandler,
    handleConfirmClicked
  }
}

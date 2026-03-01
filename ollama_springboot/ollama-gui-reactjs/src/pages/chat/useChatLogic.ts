import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import {
  CHAT_CONVERSATION_IDS_STORAGE_KEY,
  CONFIRM_FAILED_MESSAGE,
  CONFIRM_GUIDE_MESSAGE,
  CONFIRM_ONLY_PATTERN,
  REQUEST_FAILED_MESSAGE,
  TASK_ID_STORAGE_PREFIX,
  TASK_POLL_INTERVAL_MS
} from '~/constants/chat'
import {
  Message,
  RecommendTaskStateResponse
} from '~/entities/messages'
import createAssistantMessage from '~/services/createAssistantMessage'
import createUserMessage from '~/services/createUserMessage'
import getChatIndex from '~/services/getChatIndex'
import scroller from '~/services/scroller'
import Store from '~/services/store'
import { disChats, useChats } from '~/stores/chats'
import { addMessage } from '~/stores/chats/actions'
import { useConfig } from '~/stores/config'
import {
  confirmTaskRequester,
  createConversationRequester,
  createRecommendTaskRequester,
  getTaskStateRequester,
  listConversationTasksRequester
} from '~/services/requester'
import useTaskPolling from './hooks/useTaskPolling'

export type TaskStatus =
  | 'IDLE'
  | 'PENDING_CONFIRM'
  | 'CONFIRMING'
  | 'GENERATING'
  | 'DONE'
  | 'ERROR'

export interface ChatState {
  currentChatId : number|null,
  conversationId ?: string,
  taskId ?: string,
  taskStatus : TaskStatus,
  subQuestions : string[],
  finalResult : string,
  loading : boolean,
  error ?: string
}

interface ConversationTaskSnapshot {
  conversationId : string,
  activeTaskId ?: string,
  taskStatus : TaskStatus,
  subQuestions : string[],
  finalResult : string,
  loading : boolean,
  error ?: string
}

interface UseChatLogicResult {
  rowContainerRef : RefObject<HTMLDivElement>,
  textAreaRef : RefObject<HTMLTextAreaElement>,
  messages : Message[],
  hasTalk : boolean,
  taskId : string|undefined,
  taskStatus : TaskStatus,
  subQuestions : string[],
  finalResult : string,
  loading : boolean,
  error : string|undefined,
  handleDeleteChat : (chatId : number) => void,
  requestHandler : () => void,
  handleConfirmClicked : () => Promise<void>
}

const DEFAULT_VIEW_STATE : ChatState = {
  currentChatId: null,
  conversationId: undefined,
  taskId: undefined,
  taskStatus: 'IDLE',
  subQuestions: [],
  finalResult: '',
  loading: false,
  error: undefined
}

function normalizeText (value : unknown) {
  return String(value || '').trim()
}

function extractQuotedStrings (raw : string) : string[] {
  const matches = raw.match(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g) || []
  const extracted : string[] = []

  for (const match of matches) {
    if (match.length < 2) continue
    const quote = match[0]
    const body = match.slice(1, -1)
    const unescaped = quote === '\''
      ? body.replace(/\\'/g, '\'').replace(/\\\\/g, '\\')
      : body.replace(/\\"/g, '"').replace(/\\\\/g, '\\')
    const text = normalizeText(unescaped)
    if (!text) continue
    if (text === 'sub_questions' || text === 'pending_sub_questions') continue
    extracted.push(text)
  }

  return extracted
}

function parseStructuredSubQuestionString (raw : string) : unknown {
  const text = normalizeText(raw)
  if (!text || (text[0] !== '{' && text[0] !== '[')) {
    return undefined
  }

  try {
    return JSON.parse(text)
  }
  catch {
    const extracted = extractQuotedStrings(text)
    return extracted.length > 0 ? extracted : undefined
  }
}

function collectSubQuestions (raw : unknown, output : string[], seen : Set<string>) {
  if (raw == null) return

  if (typeof raw === 'string') {
    const text = normalizeText(raw)
    if (!text) return

    const parsed = parseStructuredSubQuestionString(text)
    if (parsed !== undefined) {
      collectSubQuestions(parsed, output, seen)
      return
    }

    if ((text.startsWith('{') || text.startsWith('['))
      && (text.includes('sub_questions') || text.includes('pending_sub_questions'))) {
      return
    }

    if (!seen.has(text)) {
      seen.add(text)
      output.push(text)
    }
    return
  }

  if (Array.isArray(raw)) {
    for (const item of raw) {
      collectSubQuestions(item, output, seen)
    }
    return
  }

  if (typeof raw === 'object') {
    const record = raw as Record<string, unknown>
    const preferred = [record.sub_questions, record.pending_sub_questions]
      .filter((item) => item !== undefined)
    if (preferred.length > 0) {
      for (const item of preferred) {
        collectSubQuestions(item, output, seen)
      }
      return
    }

    for (const value of Object.values(record)) {
      collectSubQuestions(value, output, seen)
    }
    return
  }

  const text = normalizeText(raw)
  if (!text || seen.has(text)) return
  seen.add(text)
  output.push(text)
}

function normalizeSubQuestions (raw : unknown) : string[] {
  const normalized : string[] = []
  collectSubQuestions(raw, normalized, new Set())
  return normalized
}

function normalizeTaskStatus (
  rawStatus : unknown,
  subQuestions : string[],
  finalResult : string,
  errorMessage : string
) : TaskStatus {
  const normalized = normalizeText(rawStatus).toUpperCase()

  if (normalized === 'PENDING_CONFIRM') {
    return 'PENDING_CONFIRM'
  }

  if (normalized === 'CONFIRMED' || normalized === 'GENERATING' || normalized === 'CONFIRMING') {
    return 'GENERATING'
  }

  if (normalized === 'DONE' || normalized === 'SUCCESS') {
    return 'DONE'
  }

  if (normalized === 'FAILED' || normalized === 'EXPIRED' || normalized === 'ERROR') {
    return 'ERROR'
  }

  if (subQuestions.length > 0) {
    return 'PENDING_CONFIRM'
  }

  if (errorMessage) {
    return 'ERROR'
  }

  if (finalResult) {
    // Non-DONE backend statuses must never be rendered as completed.
    return 'GENERATING'
  }

  return 'IDLE'
}

function taskStorageKey (conversationId : string) {
  return `${TASK_ID_STORAGE_PREFIX}${conversationId}`
}

function readConversationIdsStorage () : string[] {
  try {
    const raw = localStorage.getItem(CHAT_CONVERSATION_IDS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.map((item) => normalizeText(item))
  }
  catch {
    return []
  }
}

function writeConversationIdsStorage (value : string[]) {
  try {
    localStorage.setItem(CHAT_CONVERSATION_IDS_STORAGE_KEY, JSON.stringify(value))
  }
  catch {
    // ignore storage failures
  }
}

function toErrorMessage (error : unknown, fallback = REQUEST_FAILED_MESSAGE) {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  if (typeof error === 'string' && error.trim()) {
    return error
  }
  return fallback
}

function buildSnapshotFromTask (
  fallbackConversationId : string,
  taskState : RecommendTaskStateResponse,
  loading : boolean
) : ConversationTaskSnapshot {
  const conversationId = normalizeText(taskState.conversation_id || taskState.conversationId) || fallbackConversationId
  const taskId = normalizeText(taskState.task_id || taskState.taskId)
  const subQuestions = normalizeSubQuestions(
    taskState.pending_sub_questions
    ?? taskState.pendingSubQuestions
    ?? taskState.sub_questions
    ?? taskState.subQuestions
  )
  const finalResult = normalizeText(taskState.final_result ?? taskState.finalResult)
  const errorMessage = normalizeText(taskState.error_message ?? taskState.errorMessage)

  const taskStatus = normalizeTaskStatus(taskState.status, subQuestions, finalResult, errorMessage)

  return {
    conversationId,
    activeTaskId: taskId || undefined,
    taskStatus,
    subQuestions: taskStatus === 'PENDING_CONFIRM' ? subQuestions : [],
    finalResult: taskStatus === 'DONE' ? finalResult : '',
    loading: taskStatus === 'PENDING_CONFIRM' ? false : loading,
    error: taskStatus === 'ERROR' ? (errorMessage || REQUEST_FAILED_MESSAGE) : undefined
  }
}

function resolveConversationIdFromMessages (messages : Message[]|undefined) : string|undefined {
  if (!messages || messages.length === 0) return undefined

  for (let i = messages.length - 1; i >= 0; i--) {
    const candidate = normalizeText(messages[i].conversationId)
    if (candidate) return candidate
  }

  for (let i = messages.length - 1; i >= 0; i--) {
    const candidate = normalizeText(messages[i].sessionId)
    if (candidate) return candidate
  }

  return undefined
}

function isTerminalTaskStatus (status : TaskStatus) {
  return status === 'DONE' || status === 'ERROR' || status === 'IDLE'
}

export default function useChatLogic () : UseChatLogicResult {
  const navigate = useNavigate()
  const { chat } = useParams()
  const chats = useChats('chats')
  const { autoSaveChats, modelUrl } = useConfig('config')

  const [chatState, setChatState] = useState<ChatState>(DEFAULT_VIEW_STATE)

  const rowContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  const chatsRef = useRef(chats)
  const currentChatIdRef = useRef<number|null>(null)
  const activeConversationIdRef = useRef<string|undefined>(undefined)
  const destroyedRef = useRef(false)
  const renderCountRef = useRef(0)
  const bootstrapTokenRef = useRef(0)

  const conversationSnapshotsRef = useRef<Record<string, ConversationTaskSnapshot>>({})

  const pollTaskContextRef = useRef<{ taskId ?: string, conversationId ?: string }>({})
  const confirmInFlightRef = useRef(false)

  const applySnapshot = useCallback((snapshot : ConversationTaskSnapshot) => {
    conversationSnapshotsRef.current[snapshot.conversationId] = snapshot

    if (snapshot.activeTaskId) {
      try {
        localStorage.setItem(taskStorageKey(snapshot.conversationId), snapshot.activeTaskId)
      }
      catch {
        // ignore storage failures
      }
    }

    if (activeConversationIdRef.current !== snapshot.conversationId) {
      return
    }

    setChatState((prev) => ({
      ...prev,
      conversationId: snapshot.conversationId,
      taskId: snapshot.activeTaskId,
      taskStatus: snapshot.taskStatus,
      subQuestions: snapshot.subQuestions,
      finalResult: snapshot.finalResult,
      loading: snapshot.loading,
      error: snapshot.error
    }))
  }, [])

  const setSnapshotPatch = useCallback((conversationId : string, patch : Partial<ConversationTaskSnapshot>) => {
    const current = conversationSnapshotsRef.current[conversationId] || {
      conversationId,
      activeTaskId: undefined,
      taskStatus: 'IDLE' as TaskStatus,
      subQuestions: [],
      finalResult: '',
      loading: false,
      error: undefined
    }

    const nextTaskStatus = patch.taskStatus ?? current.taskStatus
    const nextLoading = patch.loading ?? current.loading

    applySnapshot({
      ...current,
      ...patch,
      loading: nextTaskStatus === 'PENDING_CONFIRM' ? false : nextLoading,
      conversationId
    })
  }, [applySnapshot])

  const appendAssistantMessage = useCallback((chatId : number, content : string, conversationId ?: string, taskId ?: string) => {
    const text = normalizeText(content)
    if (!text) return

    const existing = chatsRef.current[chatId] || []
    const duplicated = existing.some(
      (message) => message.role === 'assistant' && normalizeText(message.content) === text
    )
    if (duplicated) return

    disChats(addMessage({
      index: chatId,
      message: createAssistantMessage(text, conversationId, taskId)
    }))
  }, [])

  const { startPolling, stopPolling } = useTaskPolling({
    modelUrl,
    intervalMs: TASK_POLL_INTERVAL_MS,
    onTask : (taskState) => {
      const conversationId = normalizeText(taskState.conversation_id || taskState.conversationId)
        || pollTaskContextRef.current.conversationId
      if (!conversationId) return

      const snapshot = buildSnapshotFromTask(conversationId, taskState, false)
      applySnapshot(snapshot)

      if (snapshot.taskStatus === 'DONE' && snapshot.finalResult) {
        const activeChatId = currentChatIdRef.current
        if (activeChatId !== null && activeConversationIdRef.current === conversationId) {
          appendAssistantMessage(activeChatId, snapshot.finalResult, conversationId, snapshot.activeTaskId)
        }
      }

      if (isTerminalTaskStatus(snapshot.taskStatus)) {
        pollTaskContextRef.current = {}
      }
    },
    onError : (error) => {
      const conversationId = pollTaskContextRef.current.conversationId
      if (!conversationId) return
      setSnapshotPatch(conversationId, {
        loading: false,
        taskStatus: 'ERROR',
        error: toErrorMessage(error)
      })
      pollTaskContextRef.current = {}
    }
  })

  const stopActivePolling = useCallback(() => {
    stopPolling()
    pollTaskContextRef.current = {}
  }, [stopPolling])

  const startPollingForTask = useCallback((conversationId : string, taskId : string) => {
    const normalizedConversationId = normalizeText(conversationId)
    const normalizedTaskId = normalizeText(taskId)
    if (!normalizedConversationId || !normalizedTaskId) return

    pollTaskContextRef.current = {
      conversationId: normalizedConversationId,
      taskId: normalizedTaskId
    }

    startPolling(normalizedTaskId)
  }, [startPolling])

  const syncTaskState = useCallback(async (
    conversationId : string,
    taskId : string,
    loading : boolean
  ) => {
    const taskState = await getTaskStateRequester(modelUrl, taskId)
    const snapshot = buildSnapshotFromTask(conversationId, taskState, loading)
    applySnapshot(snapshot)

    if (snapshot.taskStatus === 'DONE' && snapshot.finalResult) {
      const chatId = currentChatIdRef.current
      if (chatId !== null && activeConversationIdRef.current === snapshot.conversationId) {
        appendAssistantMessage(chatId, snapshot.finalResult, snapshot.conversationId, snapshot.activeTaskId)
      }
    }

    if (snapshot.activeTaskId && snapshot.taskStatus === 'GENERATING') {
      startPollingForTask(snapshot.conversationId, snapshot.activeTaskId)
    }

    return snapshot
  }, [applySnapshot, appendAssistantMessage, modelUrl, startPollingForTask])

  const getConversationIdForChat = useCallback((chatId : number) => {
    const messages = chatsRef.current[chatId] || []
    const fromMessages = resolveConversationIdFromMessages(messages)
    if (fromMessages) return fromMessages

    const mapping = readConversationIdsStorage()
    const fromStorage = normalizeText(mapping[chatId])
    return fromStorage || undefined
  }, [])

  const setConversationIdForChat = useCallback((chatId : number, conversationId : string) => {
    const mapping = readConversationIdsStorage()
    while (mapping.length <= chatId) {
      mapping.push('')
    }
    mapping[chatId] = conversationId
    writeConversationIdsStorage(mapping)
  }, [])

  const deleteConversationIdForChat = useCallback((chatId : number) => {
    const mapping = readConversationIdsStorage()
    if (chatId < 0 || chatId >= mapping.length) return
    mapping.splice(chatId, 1)
    writeConversationIdsStorage(mapping)
  }, [])

  const loadConversationState = useCallback(async (chatId : number, conversationId : string, token : number) => {
    setSnapshotPatch(conversationId, { loading: true, error: undefined })

    const snapshot = conversationSnapshotsRef.current[conversationId]
    let candidateTaskId = normalizeText(snapshot?.activeTaskId)

    if (!candidateTaskId) {
      try {
        candidateTaskId = normalizeText(localStorage.getItem(taskStorageKey(conversationId)))
      }
      catch {
        candidateTaskId = ''
      }
    }

    if (!candidateTaskId) {
      try {
        const tasks = await listConversationTasksRequester(modelUrl, conversationId)
        const firstTask = tasks[0]
        candidateTaskId = normalizeText(firstTask?.task_id || firstTask?.taskId)
      }
      catch (error) {
        if (token !== bootstrapTokenRef.current || destroyedRef.current) return
        setSnapshotPatch(conversationId, {
          loading: false,
          taskStatus: 'ERROR',
          error: toErrorMessage(error)
        })
        return
      }
    }

    if (!candidateTaskId) {
      if (token !== bootstrapTokenRef.current || destroyedRef.current) return
      setSnapshotPatch(conversationId, {
        activeTaskId: undefined,
        taskStatus: 'IDLE',
        subQuestions: [],
        finalResult: '',
        loading: false,
        error: undefined
      })
      return
    }

    try {
      const synced = await syncTaskState(conversationId, candidateTaskId, false)
      if (token !== bootstrapTokenRef.current || destroyedRef.current) return

      if (synced.taskStatus === 'GENERATING' && synced.activeTaskId) {
        startPollingForTask(conversationId, synced.activeTaskId)
      }
    }
    catch (error) {
      if (token !== bootstrapTokenRef.current || destroyedRef.current) return
      setSnapshotPatch(conversationId, {
        loading: false,
        taskStatus: 'ERROR',
        error: toErrorMessage(error)
      })
    }

    if (token !== bootstrapTokenRef.current || destroyedRef.current) return
    scroller(rowContainerRef, 1)

    const currentMessages = chatsRef.current[chatId] || []
    if (currentMessages.length === 0) {
      return
    }
  }, [modelUrl, setSnapshotPatch, startPollingForTask, syncTaskState])

  const handleDeleteChat = useCallback((chatId : number) => {
    deleteConversationIdForChat(chatId)
    const deletedConversationId = getConversationIdForChat(chatId)
    if (deletedConversationId) {
      delete conversationSnapshotsRef.current[deletedConversationId]
    }

    if (currentChatIdRef.current !== chatId) return

    stopActivePolling()
    activeConversationIdRef.current = undefined
    setChatState({
      ...DEFAULT_VIEW_STATE,
      currentChatId: null
    })
  }, [deleteConversationIdForChat, getConversationIdForChat, stopActivePolling])

  const tryConfirmCurrentTask = useCallback(async () : Promise<boolean> => {
    const chatId = currentChatIdRef.current
    const conversationId = activeConversationIdRef.current
    if (chatId === null || !conversationId) return false

    const currentSnapshot = conversationSnapshotsRef.current[conversationId]
    const taskId = normalizeText(currentSnapshot?.activeTaskId || chatState.taskId)
    if (!taskId) {
      return false
    }

    let effectiveSnapshot = currentSnapshot
    const localStatus = effectiveSnapshot?.taskStatus || chatState.taskStatus
    const localSubQuestions = effectiveSnapshot?.subQuestions || chatState.subQuestions

    if (localStatus !== 'PENDING_CONFIRM' || localSubQuestions.length === 0) {
      try {
        effectiveSnapshot = await syncTaskState(conversationId, taskId, false)
      }
      catch (error) {
        setSnapshotPatch(conversationId, {
          taskStatus: 'ERROR',
          loading: false,
          error: toErrorMessage(error)
        })
        return true
      }
    }

    const status = effectiveSnapshot?.taskStatus || localStatus
    const subQuestions = effectiveSnapshot?.subQuestions || localSubQuestions

    if (status !== 'PENDING_CONFIRM') {
      return false
    }

    if (confirmInFlightRef.current) {
      return true
    }

    confirmInFlightRef.current = true

    setSnapshotPatch(conversationId, {
      loading: true,
      taskStatus: 'CONFIRMING',
      error: undefined
    })

    try {
      const confirmed = await confirmTaskRequester(modelUrl, taskId, 'confirm', {
        sub_questions: subQuestions,
        comment: 'confirm'
      })

      const snapshot = buildSnapshotFromTask(conversationId, confirmed, false)
      applySnapshot(snapshot)

      if (snapshot.taskStatus === 'DONE' && snapshot.finalResult) {
        appendAssistantMessage(chatId, snapshot.finalResult, snapshot.conversationId, snapshot.activeTaskId)
      }

      if (snapshot.activeTaskId && snapshot.taskStatus === 'GENERATING') {
        startPollingForTask(snapshot.conversationId, snapshot.activeTaskId)
      }
      return true
    }
    catch (confirmError) {
      try {
        const recovered = await getTaskStateRequester(modelUrl, taskId)
        const snapshot = buildSnapshotFromTask(conversationId, recovered, false)
        applySnapshot(snapshot)

        if (snapshot.taskStatus === 'DONE' && snapshot.finalResult) {
          appendAssistantMessage(chatId, snapshot.finalResult, snapshot.conversationId, snapshot.activeTaskId)
        }

        if (snapshot.activeTaskId && snapshot.taskStatus === 'GENERATING') {
          startPollingForTask(snapshot.conversationId, snapshot.activeTaskId)
        }

        if (snapshot.taskStatus !== 'ERROR') {
          return true
        }
      }
      catch {
        // fall through to explicit confirm error
      }

      setSnapshotPatch(conversationId, {
        loading: false,
        taskStatus: 'ERROR',
        error: toErrorMessage(confirmError, CONFIRM_FAILED_MESSAGE)
      })
      return true
    }
    finally {
      confirmInFlightRef.current = false
    }
  }, [
    appendAssistantMessage,
    applySnapshot,
    chatState.subQuestions,
    chatState.taskId,
    chatState.taskStatus,
    modelUrl,
    setSnapshotPatch,
    startPollingForTask,
    syncTaskState
  ])

  const requestHandler = useCallback(() => {
    const chatId = currentChatIdRef.current
    if (chatId === null) return

    if (chatState.taskStatus === 'CONFIRMING') return

    const text = normalizeText(textAreaRef.current?.value)
    if (!text) return

    if (CONFIRM_ONLY_PATTERN.test(text)) {
      if (textAreaRef.current) {
        textAreaRef.current.value = ''
        textAreaRef.current.focus()
      }

      void (async () => {
        const handled = await tryConfirmCurrentTask()
        if (handled) {
          return
        }
        appendAssistantMessage(chatId, CONFIRM_GUIDE_MESSAGE, activeConversationIdRef.current, chatState.taskId)
      })()
      return
    }

    void (async () => {
      let conversationId = activeConversationIdRef.current
      if (!conversationId) {
        try {
          const createdConversation = await createConversationRequester(modelUrl)
          conversationId = normalizeText(createdConversation.conversation_id)
          if (!conversationId) {
            throw new Error('Invalid conversation id returned from backend')
          }
          activeConversationIdRef.current = conversationId
          setConversationIdForChat(chatId, conversationId)
        }
        catch (error) {
          setChatState((prev) => ({
            ...prev,
            taskStatus: 'ERROR',
            error: toErrorMessage(error)
          }))
          return
        }
      }

      const taskConversationId = conversationId

      disChats(addMessage({
        index: chatId,
        message: createUserMessage(text, taskConversationId)
      }))

      if (textAreaRef.current) {
        textAreaRef.current.value = ''
        textAreaRef.current.focus()
      }

      setSnapshotPatch(taskConversationId, {
        taskStatus: 'GENERATING',
        loading: true,
        error: undefined,
        subQuestions: [],
        finalResult: ''
      })

      try {
        const taskState = await createRecommendTaskRequester(modelUrl, {
          query: text,
          conversation_id: taskConversationId,
          timeout: 60,
          max_iterations: 3
        })

        const snapshot = buildSnapshotFromTask(taskConversationId, taskState, false)
        applySnapshot(snapshot)

        if (snapshot.taskStatus === 'DONE' && snapshot.finalResult) {
          appendAssistantMessage(chatId, snapshot.finalResult, snapshot.conversationId, snapshot.activeTaskId)
        }

        if (snapshot.activeTaskId && snapshot.taskStatus === 'GENERATING') {
          startPollingForTask(snapshot.conversationId, snapshot.activeTaskId)
        }
      }
      catch (error) {
        setSnapshotPatch(taskConversationId, {
          loading: false,
          taskStatus: 'ERROR',
          error: toErrorMessage(error)
        })
      }
    })()
  }, [
    appendAssistantMessage,
    applySnapshot,
    chatState.taskId,
    chatState.taskStatus,
    modelUrl,
    setConversationIdForChat,
    setSnapshotPatch,
    startPollingForTask,
    tryConfirmCurrentTask
  ])

  const handleConfirmClicked = useCallback(async () => {
    const handled = await tryConfirmCurrentTask()
    if (handled) {
      return
    }

    const conversationId = activeConversationIdRef.current
    if (!conversationId) {
      return
    }

    setSnapshotPatch(conversationId, {
      taskStatus: 'ERROR',
      loading: false,
      error: 'No pending confirmation task found. Please submit a new query first.'
    })
  }, [
    setSnapshotPatch,
    tryConfirmCurrentTask
  ])

  useEffect(() => {
    chatsRef.current = chats
  }, [chats])

  useEffect(() => {
    if (!autoSaveChats) return
    renderCountRef.current += 1
    if (renderCountRef.current < 3) return
    Store.set('chats', chats)
  }, [autoSaveChats, chats])

  useEffect(() => {
    const chatId = getChatIndex()
    const token = ++bootstrapTokenRef.current

    stopActivePolling()

    if (chatId < 0 || chatId > chatsRef.current.length) {
      navigate(ROUTES.ROOT)
      return
    }

    currentChatIdRef.current = chatId
    setChatState((prev) => ({ ...prev, currentChatId: chatId, loading: true, error: undefined }))

    const currentMessages = chatsRef.current[chatId] || []
    const resolvedConversationId = resolveConversationIdFromMessages(currentMessages) || getConversationIdForChat(chatId)

    if (!resolvedConversationId) {
      activeConversationIdRef.current = undefined
      setChatState({
        ...DEFAULT_VIEW_STATE,
        currentChatId: chatId,
        loading: false
      })
      return
    }

    activeConversationIdRef.current = resolvedConversationId
    setConversationIdForChat(chatId, resolvedConversationId)

    const existingSnapshot = conversationSnapshotsRef.current[resolvedConversationId]
    if (existingSnapshot) {
      applySnapshot(existingSnapshot)
    } else {
      setChatState((prev) => ({
        ...prev,
        currentChatId: chatId,
        conversationId: resolvedConversationId,
        taskStatus: 'IDLE',
        subQuestions: [],
        finalResult: '',
        loading: true,
        error: undefined
      }))
    }

    void loadConversationState(chatId, resolvedConversationId, token)
  }, [chat, chats.length, getConversationIdForChat, loadConversationState, navigate, setConversationIdForChat, stopActivePolling, applySnapshot])

  useEffect(() => {
    scroller(rowContainerRef, 1)
  }, [chats, chatState.currentChatId])

  useEffect(() => {
    return () => {
      destroyedRef.current = true
      stopActivePolling()
    }
  }, [stopActivePolling])

  const messages = useMemo(() => {
    if (chatState.currentChatId === null) return []
    return chats[chatState.currentChatId] || []
  }, [chatState.currentChatId, chats])

  return {
    rowContainerRef,
    textAreaRef,
    messages,
    hasTalk: messages.length > 0,
    taskId: chatState.taskId,
    taskStatus: chatState.taskStatus,
    subQuestions: chatState.subQuestions,
    finalResult: chatState.finalResult,
    loading: chatState.loading,
    error: chatState.error,
    handleDeleteChat,
    requestHandler,
    handleConfirmClicked
  }
}

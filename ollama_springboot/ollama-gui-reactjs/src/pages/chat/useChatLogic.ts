import { userStorageKey } from '~/services/auth'
import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import {
  CHAT_CONVERSATION_IDS_STORAGE_KEY,
  CONFIRM_GUIDE_MESSAGE,
  CONFIRM_ONLY_PATTERN,
  REQUEST_FAILED_MESSAGE
} from '~/constants/chat'
import {
  ChatStreamEvent,
  Clarification,
  RoutingMode,
  RoutingOption,
  ImageAttachment,
  Message,
  RetrievedImage,
} from '~/entities/messages'
import createAssistantMessage from '~/services/createAssistantMessage'
import createUserMessage from '~/services/createUserMessage'
import getChatIndex from '~/services/getChatIndex'
import scroller from '~/services/scroller'
import Store from '~/services/store'
import { disChats, useChats } from '~/stores/chats'
import { addMessage, updateMessageContent } from '~/stores/chats/actions'
import { useConfig } from '~/stores/config'
import {
  cancelWaitingTaskRequester,
  listConversationTasksRequester,
  normalizeClarification,
  streamClarifyRequester,
  resolveApiBase,
  streamChatConfirmRequester,
  streamChatRequester
} from '~/services/requester'
import { routingFields } from '~/services/routing'

export type TaskStatus =
  | 'IDLE'
  | 'PENDING_CONFIRM'
  | 'PENDING_INPUT'
  | 'CANCELLED'
  | 'CONFIRMING'
  | 'GENERATING'
  | 'DONE'
  | 'INCOMPLETE'
  | 'ERROR'

export interface ChatState {
  currentChatId : number|null,
  conversationId ?: string,
  taskId ?: string,
  taskStatus : TaskStatus,
  subQuestions : string[],
  finalResult : string,
  loading : boolean,
  error ?: string,
  pendingInput ?: Clarification,
  nextStep ?: string
}

interface ConversationTaskSnapshot {
  conversationId : string,
  activeTaskId ?: string,
  taskStatus : TaskStatus,
  subQuestions : string[],
  finalResult : string,
  loading : boolean,
  error ?: string,
  pendingInput ?: Clarification,
  nextStep ?: string
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
  pendingImages : ImageAttachment[],
  apiBaseUrl : string,
  routingMode : RoutingMode,
  routingClarification : Clarification|undefined,
  pendingInput : Clarification|undefined,
  nextStep : string,
  setRoutingMode : (mode : RoutingMode) => void,
  handleRoutingChoice : (option : RoutingOption|undefined, answer : string) => Promise<void>,
  handleRoutingDismiss : () => void,
  handleInputSubmit : (answer : string) => Promise<void>,
  handleCancelWaiting : () => Promise<void>,
  handleDeleteChat : (chatId : number) => void,
  requestHandler : () => void,
  handleConfirmClicked : () => Promise<void>,
  handleImagesSelected : (images : ImageAttachment[]) => void,
  handleImageRemoved : (index : number) => void
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

function createEmptySnapshot (conversationId : string) : ConversationTaskSnapshot {
  return {
    conversationId,
    activeTaskId: undefined,
    taskStatus: 'IDLE',
    subQuestions: [],
    finalResult: '',
    loading: false,
    error: undefined
  }
}

function readConversationIdsStorage () : string[] {
  try {
    const raw = localStorage.getItem(userStorageKey(CHAT_CONVERSATION_IDS_STORAGE_KEY) || 'reco:no-session')
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
    const key = userStorageKey(CHAT_CONVERSATION_IDS_STORAGE_KEY)
    if (key) localStorage.setItem(key, JSON.stringify(value))
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

interface PendingTurn {
  chatId : number,
  text : string,
  images : ImageAttachment[],
  message : Message,
  originConversationId ?: string,
  committed : boolean
}

type StreamSender = (onEvent : (event : ChatStreamEvent) => void, signal : AbortSignal) => Promise<void>

export default function useChatLogic () : UseChatLogicResult {
  const navigate = useNavigate()
  const { chat } = useParams()
  const chats = useChats('chats')
  const { autoSaveChats, modelUrl, modelName } = useConfig('config')
  const [chatState, setChatState] = useState<ChatState>(DEFAULT_VIEW_STATE)
  const [pendingImages, setPendingImages] = useState<ImageAttachment[]>([])
  const [routingMode, setRoutingMode] = useState<RoutingMode>('explicit')
  const [routingClarification, setRoutingClarification] = useState<Clarification>()
  const [pendingTurn, setPendingTurn] = useState<PendingTurn>()
  const rowContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const chatsRef = useRef(chats)
  const renderCountRef = useRef(0)
  const currentChatIdRef = useRef<number|null>(null)
  const activeConversationIdRef = useRef<string>()
  const activeStreamRunIdRef = useRef(0)
  const streamAbortControllerRef = useRef<AbortController|null>(null)
  const pendingTurnRef = useRef<PendingTurn>()
  const internalNavigationRef = useRef<number>()
  const conversationSnapshotsRef = useRef<Record<string, ConversationTaskSnapshot>>({})

  const appendLocal = useCallback((chatId : number, message : Message) => {
    const next = [...chatsRef.current]
    next[chatId] = [...(next[chatId] || []), message]
    chatsRef.current = next
    disChats(addMessage({ index: chatId, message }))
  }, [])

  const applySnapshot = useCallback((snapshot : ConversationTaskSnapshot) => {
    conversationSnapshotsRef.current[snapshot.conversationId] = snapshot
    if (activeConversationIdRef.current !== snapshot.conversationId) return
    setChatState((prev) => ({
      ...prev,
      conversationId: snapshot.conversationId,
      taskId: snapshot.activeTaskId,
      taskStatus: snapshot.taskStatus,
      subQuestions: snapshot.subQuestions,
      finalResult: snapshot.finalResult,
      loading: snapshot.loading,
      error: snapshot.error,
      pendingInput: snapshot.pendingInput,
      nextStep: snapshot.nextStep
    }))
  }, [])

  const setSnapshotPatch = useCallback((conversationId : string, patch : Partial<ConversationTaskSnapshot>) => {
    const current = conversationSnapshotsRef.current[conversationId] || createEmptySnapshot(conversationId)
    const status = patch.taskStatus ?? current.taskStatus
    applySnapshot({
      ...current, ...patch, conversationId,
      loading: status === 'PENDING_CONFIRM' || status === 'PENDING_INPUT' ? false : (patch.loading ?? current.loading)
    })
  }, [applySnapshot])

  const setConversationIdForChat = useCallback((chatId : number, conversationId : string) => {
    const ids = readConversationIdsStorage()
    while (ids.length <= chatId) ids.push('')
    ids[chatId] = conversationId
    writeConversationIdsStorage(ids)
  }, [])

  const stopActiveStream = useCallback(() => {
    const conversationId = activeConversationIdRef.current
    if (streamAbortControllerRef.current && conversationId && conversationSnapshotsRef.current[conversationId]?.loading) {
      setSnapshotPatch(conversationId, {
        loading: false, taskStatus: 'INCOMPLETE',
        error: 'Generation stopped before completion; partial text is preserved.'
      })
    }
    activeStreamRunIdRef.current += 1
    streamAbortControllerRef.current?.abort()
    streamAbortControllerRef.current = null
  }, [setSnapshotPatch])

  const clearTurn = useCallback(() => {
    pendingTurnRef.current = undefined
    setPendingTurn(undefined)
    setRoutingClarification(undefined)
  }, [])

  const restoreTurn = useCallback(() => {
    const turn = pendingTurnRef.current
    if (turn && !turn.committed) {
      if (textAreaRef.current) textAreaRef.current.value = turn.text
      setPendingImages(turn.images)
    }
    clearTurn()
  }, [clearTurn])

  // Routing can finish before a conversation exists. Do not persist a local user
  // message until the server names its actual destination; a clarification can
  // then be retried without duplicating or moving committed history.
  const runStream = useCallback(async (
    send : StreamSender,
    turn ?: PendingTurn,
    resumeConversationId ?: string,
    resumeTaskId ?: string
  ) => {
    if (streamAbortControllerRef.current) return
    const controller = new AbortController()
    streamAbortControllerRef.current = controller
    const runId = ++activeStreamRunIdRef.current
    let chatId = turn?.chatId ?? currentChatIdRef.current ?? 0
    let conversationId = resumeConversationId
    let taskId = resumeTaskId
    let text = ''
    let images : RetrievedImage[] = []
    let assistantTime : number|undefined
    let terminal = false
    let resumeAccepted = false
    const resumeSnapshot = resumeConversationId ? conversationSnapshotsRef.current[resumeConversationId] : undefined
    const active = () => activeStreamRunIdRef.current === runId
    const update = (patch : Partial<ConversationTaskSnapshot>) => {
      if (conversationId) setSnapshotPatch(conversationId, { activeTaskId: taskId, ...patch })
      else setChatState((prev) => ({ ...prev, ...patch, taskId, loading: patch.loading ?? prev.loading }))
    }
    const commitDestination = (destination ?: string) => {
      if (!destination) return
      conversationId = destination
      if (!turn || turn.committed) return
      const source = turn.originConversationId
      if (source && source !== destination) {
        const ids = readConversationIdsStorage()
        const existing = chatsRef.current.findIndex((messages, index) =>
          (resolveConversationIdFromMessages(messages) || ids[index]) === destination)
        chatId = existing >= 0 ? existing : chatsRef.current.length
      }
      turn.chatId = chatId
      turn.committed = true
      turn.message = { ...turn.message, conversationId: destination, sessionId: taskId }
      setConversationIdForChat(chatId, destination)
      activeConversationIdRef.current = destination
      const changedChat = currentChatIdRef.current !== chatId
      currentChatIdRef.current = chatId
      setChatState((prev) => ({ ...prev, currentChatId: chatId, conversationId: destination }))
      appendLocal(chatId, turn.message)
      setPendingTurn(undefined)
      if (changedChat) {
        internalNavigationRef.current = chatId
        navigate(`${ROUTES.CHAT}/${chatId + 1}`)
      }
    }
    const syncAssistant = () => {
      if (!text) return
      if (assistantTime === undefined) {
        const message = createAssistantMessage(text, conversationId, taskId, images)
        // Timestamp identity must differ even when a stub sends both turns in one tick.
        message.time = Math.max(message.time, (turn?.message.time || 0) + 1)
        assistantTime = message.time
        appendLocal(chatId, message)
      } else {
        disChats(updateMessageContent({
          index: chatId, time: assistantTime, content: text,
          conversationId, sessionId: taskId, retrievedImages: images
        }))
      }
    }
    update({ loading: true, taskStatus: 'GENERATING', error: undefined, nextStep: '', pendingInput: resumeSnapshot?.pendingInput })
    try {
      await send((event) => {
        if (!active() || terminal) return
        if (event.type === 'routing_clarification') {
          terminal = true
          setRoutingClarification(event.clarification)
          setChatState((prev) => ({ ...prev, loading: false, taskStatus: 'IDLE', taskId: undefined, error: undefined }))
          return
        }
        // A preflight failure must not commit the answer or discard the waiting form.
        // The server sends the first meta only after Python accepts this exact resume.
        if (event.type === 'error' && resumeSnapshot?.pendingInput && !resumeAccepted) {
          terminal = true
          update({ loading: false, taskStatus: 'PENDING_INPUT', pendingInput: resumeSnapshot.pendingInput, error: event.message, nextStep: '' })
          return
        }
        if (resumeSnapshot?.pendingInput) resumeAccepted = true
        taskId = normalizeText(event.task_id) || taskId
        commitDestination(normalizeText(event.conversation_id) || undefined)
        if (event.type === 'route_decision') {
          update({ nextStep: event.next_step })
          return
        }
        if (event.type === 'meta' || event.type === 'node' || event.type === 'state') {
          update({ loading: true, taskStatus: 'GENERATING', pendingInput: undefined })
          return
        }
        if (event.type === 'token') {
          text += event.delta
          syncAssistant()
          update({ finalResult: text })
          return
        }
        if (event.type === 'awaiting_confirmation') {
          terminal = true
          update({ loading: false, taskStatus: 'PENDING_CONFIRM', subQuestions: normalizeSubQuestions(event.sub_questions), pendingInput: undefined })
          clearTurn()
          return
        }
        if (event.type === 'awaiting_input') {
          terminal = true
          if (!taskId || !event.pending_input.clarification_id) {
            update({ loading: false, taskStatus: 'ERROR', error: 'The request for more information is missing its task identifier.' })
          } else {
            update({ loading: false, taskStatus: 'PENDING_INPUT', pendingInput: event.pending_input, subQuestions: [] })
          }
          clearTurn()
          return
        }
        if (event.type === 'final') {
          terminal = true
          text = event.final_answer || text
          images = event.retrieved_images
          syncAssistant()
          const incomplete = ['incomplete', 'expired', 'cancelled'].includes(event.status.toLowerCase())
          update({ loading: false, taskStatus: incomplete ? 'INCOMPLETE' : 'DONE', finalResult: text, subQuestions: [], pendingInput: undefined, nextStep: '' })
          clearTurn()
          return
        }
        if (event.type === 'error') {
          terminal = true
          update({ loading: false, taskStatus: 'ERROR', pendingInput: undefined, error: event.message, nextStep: '' })
          if (turn && !turn.committed) restoreTurn()
          else clearTurn()
        }
      }, controller.signal)
      if (active() && !terminal) {
        update({
          loading: false, taskStatus: resumeSnapshot?.pendingInput && !resumeAccepted ? 'PENDING_INPUT' : 'INCOMPLETE',
          pendingInput: resumeAccepted ? undefined : resumeSnapshot?.pendingInput,
          error: 'Stream ended before a completion event; partial text is preserved.'
        })
        if (turn && !turn.committed) restoreTurn()
        else clearTurn()
      }
    } catch (error) {
      if (active() && !controller.signal.aborted) {
        update({
          loading: false, taskStatus: resumeSnapshot?.pendingInput && !resumeAccepted ? 'PENDING_INPUT' : 'ERROR',
          pendingInput: resumeAccepted ? undefined : resumeSnapshot?.pendingInput,
          error: toErrorMessage(error), nextStep: ''
        })
        if (turn && !turn.committed) restoreTurn()
        else clearTurn()
      }
    } finally {
      if (streamAbortControllerRef.current === controller) streamAbortControllerRef.current = null
    }
  }, [appendLocal, clearTurn, navigate, restoreTurn, setConversationIdForChat, setSnapshotPatch])

  const submitTurn = useCallback(async (turn : PendingTurn, mode : RoutingMode, destination ?: string) => {
    setRoutingClarification(undefined)
    await runStream(async (onEvent, signal) => {
      const payload = { model: modelName, stream: true, messages: [{ role: 'user' as const, content: turn.text }], images: turn.images }
      await streamChatRequester(modelUrl, { ...payload, ...routingFields(mode, destination) }, onEvent, signal)
    }, turn)
  }, [modelName, modelUrl, runStream])

  const handleConfirmClicked = useCallback(async () => {
    const conversationId = activeConversationIdRef.current
    const snapshot = conversationId ? conversationSnapshotsRef.current[conversationId] : undefined
    if (!conversationId || !snapshot?.activeTaskId || snapshot.taskStatus !== 'PENDING_CONFIRM') return
    await runStream((onEvent, signal) => streamChatConfirmRequester(modelUrl, {
      model: modelName, task_id: snapshot.activeTaskId, session_id: snapshot.activeTaskId,
      conversation_id: conversationId, action: 'confirm', sub_questions: snapshot.subQuestions, comment: 'confirm'
    }, onEvent, signal), undefined, conversationId, snapshot.activeTaskId)
  }, [modelName, modelUrl, runStream])

  const requestHandler = useCallback(() => {
    const chatId = currentChatIdRef.current
    if (chatId === null || streamAbortControllerRef.current || pendingTurnRef.current || chatState.taskStatus === 'PENDING_INPUT') return
    const text = normalizeText(textAreaRef.current?.value)
    const images = [...pendingImages]
    if (!text && !images.length) return
    if (!images.length && CONFIRM_ONLY_PATTERN.test(text)) {
      if (chatState.taskStatus === 'PENDING_CONFIRM') void handleConfirmClicked()
      else appendLocal(chatId, createAssistantMessage(CONFIRM_GUIDE_MESSAGE, activeConversationIdRef.current))
      if (textAreaRef.current) textAreaRef.current.value = ''
      return
    }
    if (chatState.taskStatus === 'PENDING_CONFIRM') return
    const turn : PendingTurn = {
      chatId, text, images, committed: false,
      originConversationId: activeConversationIdRef.current,
      message: createUserMessage(text || 'Analyze the attached image.', undefined, undefined, images)
    }
    pendingTurnRef.current = turn
    setPendingTurn({ ...turn })
    if (textAreaRef.current) textAreaRef.current.value = ''
    setPendingImages([])
    // A blank chat is an explicit new-conversation action unless auto was chosen.
    void submitTurn(turn, routingMode, turn.originConversationId)
  }, [appendLocal, chatState.taskStatus, handleConfirmClicked, pendingImages, routingMode, submitTurn])

  const handleRoutingChoice = useCallback(async (option : RoutingOption|undefined, answer : string) => {
    const turn = pendingTurnRef.current
    if (!turn || turn.committed || streamAbortControllerRef.current) return
    if (answer.trim()) {
      turn.text = `${turn.text}\n\n${answer.trim()}`.trim()
      turn.message = { ...turn.message, content: turn.text }
      setPendingTurn({ ...turn })
    }
    if (option) await submitTurn(turn, option.kind === 'new' ? 'new' : 'explicit', option.conversation_id)
    else if (answer.trim()) await submitTurn(turn, routingMode, turn.originConversationId)
  }, [routingMode, submitTurn])

  const handleInputSubmit = useCallback(async (answer : string) => {
    const conversationId = activeConversationIdRef.current
    const snapshot = conversationId ? conversationSnapshotsRef.current[conversationId] : undefined
    const pending = snapshot?.pendingInput
    const taskId = snapshot?.activeTaskId
    if (!conversationId || !taskId || !pending?.clarification_id || !answer.trim() || streamAbortControllerRef.current) return
    if (answer.length > 4000) {
      setSnapshotPatch(conversationId, { error: 'Please limit the clarification to 4000 characters.' })
      return
    }
    const turn : PendingTurn = {
      chatId: currentChatIdRef.current ?? 0,
      text: answer.trim(), images: [], committed: false, originConversationId: conversationId,
      message: createUserMessage(answer.trim(), conversationId, taskId)
    }
    await runStream((onEvent, signal) => streamClarifyRequester(modelUrl, {
      task_id: taskId, clarification_id: pending.clarification_id, answer: answer.trim()
    }, onEvent, signal), turn, conversationId, taskId)
  }, [modelUrl, runStream, setSnapshotPatch])

  const handleCancelWaiting = useCallback(async () => {
    const conversationId = activeConversationIdRef.current
    const snapshot = conversationId ? conversationSnapshotsRef.current[conversationId] : undefined
    if (!conversationId || !snapshot?.activeTaskId || streamAbortControllerRef.current) return
    try {
      await cancelWaitingTaskRequester(modelUrl, snapshot.activeTaskId)
      setSnapshotPatch(conversationId, { loading: false, taskStatus: 'CANCELLED', pendingInput: undefined, subQuestions: [], error: undefined })
    } catch (error) {
      setSnapshotPatch(conversationId, { error: toErrorMessage(error) })
    }
  }, [modelUrl, setSnapshotPatch])

  const handleDeleteChat = useCallback((chatId : number) => {
    const ids = readConversationIdsStorage()
    const conversationId = resolveConversationIdFromMessages(chatsRef.current[chatId]) || ids[chatId]
    ids.splice(chatId, 1)
    writeConversationIdsStorage(ids)
    if (conversationId) delete conversationSnapshotsRef.current[conversationId]
    if (currentChatIdRef.current === chatId) {
      stopActiveStream()
      clearTurn()
      setPendingImages([])
      activeConversationIdRef.current = undefined
      setChatState(DEFAULT_VIEW_STATE)
    }
  }, [clearTurn, stopActiveStream])

  useEffect(() => { chatsRef.current = chats }, [chats])
  useEffect(() => {
    if (!autoSaveChats) return
    renderCountRef.current += 1
    if (renderCountRef.current < 3) return
    Store.set('chats', chats.map((messages) => messages.map((message) => ({ ...message, images: undefined }))))
  }, [autoSaveChats, chats])

  useEffect(() => {
    const chatId = getChatIndex()
    if (internalNavigationRef.current === chatId) {
      internalNavigationRef.current = undefined
      return
    }
    stopActiveStream()
    clearTurn()
    setPendingImages([])
    if (chatId < 0 || chatId > chatsRef.current.length) {
      navigate(ROUTES.ROOT)
      return
    }
    currentChatIdRef.current = chatId
    const conversationId = resolveConversationIdFromMessages(chatsRef.current[chatId]) || readConversationIdsStorage()[chatId]
    setRoutingMode('explicit')
    activeConversationIdRef.current = conversationId
    setChatState({ ...DEFAULT_VIEW_STATE, currentChatId: chatId, conversationId })
    if (!conversationId) return
    const snapshot = conversationSnapshotsRef.current[conversationId]
    if (snapshot) {
      applySnapshot(snapshot)
      return
    }
    const controller = new AbortController()
    void listConversationTasksRequester(modelUrl, conversationId, controller.signal).then((tasks) => {
      if (controller.signal.aborted || conversationSnapshotsRef.current[conversationId] || !tasks.length) return
      const task = tasks[0]
      const rawStatus = String(task.status).toUpperCase()
      const status : TaskStatus = rawStatus === 'PENDING_INPUT' ? 'PENDING_INPUT'
        : rawStatus === 'PENDING_CONFIRM' ? 'PENDING_CONFIRM'
          : rawStatus === 'GENERATING' || rawStatus === 'CONFIRMED' ? 'GENERATING'
            : rawStatus === 'DONE' ? 'DONE' : rawStatus === 'CANCELLED' ? 'CANCELLED' : 'INCOMPLETE'
      applySnapshot({
        ...createEmptySnapshot(conversationId),
        activeTaskId: task.task_id || task.taskId, taskStatus: status,
        subQuestions: normalizeSubQuestions(task.sub_questions ?? task.subQuestions ?? task.pending_sub_questions),
        pendingInput: task.pending_input || task.pendingInput ? normalizeClarification(task.pending_input ?? task.pendingInput, 'execution') : undefined,
        finalResult: task.final_result || task.finalResult || '',
        error: task.error_message || task.errorMessage
      })
    }).catch(() => { /* Existing local history stays usable if refresh fails. */ })
    return () => controller.abort()
  }, [chat, applySnapshot, clearTurn, modelUrl, navigate, stopActiveStream])

  useEffect(() => { scroller(rowContainerRef, 1) }, [chats, chatState.currentChatId, pendingTurn])
  useEffect(() => () => { stopActiveStream() }, [stopActiveStream])

  const messages = useMemo(() => {
    const committed = chatState.currentChatId === null ? [] : (chats[chatState.currentChatId] || [])
    return pendingTurn && !pendingTurn.committed && pendingTurn.chatId === chatState.currentChatId
      ? [...committed, pendingTurn.message] : committed
  }, [chatState.currentChatId, chats, pendingTurn])

  return {
    rowContainerRef, textAreaRef, messages, hasTalk: messages.length > 0,
    taskId: chatState.taskId, taskStatus: chatState.taskStatus, subQuestions: chatState.subQuestions,
    finalResult: chatState.finalResult, loading: chatState.loading, error: chatState.error,
    pendingImages, apiBaseUrl: resolveApiBase(modelUrl), routingMode, setRoutingMode,
    routingClarification, pendingInput: chatState.pendingInput, nextStep: chatState.nextStep || '',
    handleDeleteChat, requestHandler, handleConfirmClicked, handleRoutingChoice,
    handleRoutingDismiss: restoreTurn, handleInputSubmit, handleCancelWaiting,
    handleImagesSelected: (images) => setPendingImages((current) => [...current, ...images].slice(0, 4)),
    handleImageRemoved: (index) => setPendingImages((current) => current.filter((_, i) => i !== index))
  }
}

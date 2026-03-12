import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import {
  CHAT_CONVERSATION_IDS_STORAGE_KEY,
  CONFIRM_FAILED_MESSAGE,
  CONFIRM_GUIDE_MESSAGE,
  CONFIRM_ONLY_PATTERN,
  REQUEST_FAILED_MESSAGE
} from '~/constants/chat'
import {
  ChatStreamEvent,
  Message,
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
  createConversationRequester,
  streamChatConfirmRequester,
  streamChatRequester
} from '~/services/requester'

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

function streamEventType (event : ChatStreamEvent) {
  return event.type
}

function streamEventTaskId (event : ChatStreamEvent) {
  return normalizeText(event.task_id) || undefined
}

function streamEventConversationId (event : ChatStreamEvent) {
  return normalizeText(event.conversation_id || event.session_id) || undefined
}

export default function useChatLogic () : UseChatLogicResult {
  const navigate = useNavigate()
  const { chat } = useParams()
  const chats = useChats('chats')
  const { autoSaveChats, modelUrl, modelName } = useConfig('config')

  const [chatState, setChatState] = useState<ChatState>(DEFAULT_VIEW_STATE)

  const rowContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  const chatsRef = useRef(chats)
  const currentChatIdRef = useRef<number|null>(null)
  const activeConversationIdRef = useRef<string|undefined>(undefined)
  const renderCountRef = useRef(0)
  const activeStreamRunIdRef = useRef(0)
  const streamAbortControllerRef = useRef<AbortController|null>(null)
  const confirmInFlightRef = useRef(false)
  const conversationSnapshotsRef = useRef<Record<string, ConversationTaskSnapshot>>({})

  const applySnapshot = useCallback((snapshot : ConversationTaskSnapshot) => {
    conversationSnapshotsRef.current[snapshot.conversationId] = snapshot

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
    const current = conversationSnapshotsRef.current[conversationId] || createEmptySnapshot(conversationId)
    const nextTaskStatus = patch.taskStatus ?? current.taskStatus
    const nextLoading = patch.loading ?? current.loading

    applySnapshot({
      ...current,
      ...patch,
      loading: nextTaskStatus === 'PENDING_CONFIRM' ? false : nextLoading,
      conversationId
    })
  }, [applySnapshot])

  const stopActiveStream = useCallback(() => {
    activeStreamRunIdRef.current += 1
    streamAbortControllerRef.current?.abort()
    streamAbortControllerRef.current = null
  }, [])

  const beginStream = useCallback(() => {
    const runId = activeStreamRunIdRef.current + 1
    activeStreamRunIdRef.current = runId
    const controller = new AbortController()
    streamAbortControllerRef.current = controller
    return {
      runId,
      controller
    }
  }, [])

  const isStreamActive = useCallback((runId : number) => {
    return activeStreamRunIdRef.current === runId
  }, [])

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

  const handleDeleteChat = useCallback((chatId : number) => {
    const deletedConversationId = getConversationIdForChat(chatId)
    deleteConversationIdForChat(chatId)
    if (deletedConversationId) {
      delete conversationSnapshotsRef.current[deletedConversationId]
    }

    if (currentChatIdRef.current !== chatId) return

    stopActiveStream()
    activeConversationIdRef.current = undefined
    setChatState({
      ...DEFAULT_VIEW_STATE,
      currentChatId: null
    })
  }, [deleteConversationIdForChat, getConversationIdForChat, stopActiveStream])

  const tryConfirmCurrentTask = useCallback(async () : Promise<boolean> => {
    const chatId = currentChatIdRef.current
    const conversationId = activeConversationIdRef.current
    if (chatId === null || !conversationId) return false

    const snapshot = conversationSnapshotsRef.current[conversationId]
    const taskId = normalizeText(snapshot?.activeTaskId || chatState.taskId || conversationId)
    const subQuestions = snapshot?.subQuestions || chatState.subQuestions
    const status = snapshot?.taskStatus || chatState.taskStatus

    if (!taskId || status !== 'PENDING_CONFIRM' || subQuestions.length === 0) {
      return false
    }

    if (confirmInFlightRef.current) {
      return true
    }

    stopActiveStream()
    confirmInFlightRef.current = true
    setSnapshotPatch(conversationId, {
      activeTaskId: taskId,
      loading: true,
      taskStatus: 'CONFIRMING',
      error: undefined
    })

    const {
      runId: streamRunId,
      controller: streamAbort
    } = beginStream()

    let streamedTaskId : string|undefined = taskId
    let streamedConversationId = conversationId
    let streamedText = ''
    let streamMessageTime : number|undefined
    let streamReachedTerminal = false

    const ensureStreamAssistant = () => {
      if (streamMessageTime !== undefined) return
      const draft = createAssistantMessage('', streamedConversationId, streamedTaskId)
      streamMessageTime = draft.time
      disChats(addMessage({
        index: chatId,
        message: draft
      }))
    }

    const syncStreamAssistant = () => {
      if (streamMessageTime === undefined) return
      disChats(updateMessageContent({
        index: chatId,
        time: streamMessageTime,
        content: streamedText,
        conversationId: streamedConversationId,
        sessionId: streamedTaskId
      }))
    }

    try {
      await streamChatConfirmRequester(
        modelUrl,
        {
          model: modelName,
          task_id: taskId,
          session_id: taskId,
          conversation_id: conversationId,
          action: 'confirm',
          sub_questions: subQuestions,
          comment: 'confirm'
        },
        (event : ChatStreamEvent) => {
          if (!isStreamActive(streamRunId)) {
            return
          }

          const eventType = streamEventType(event)
          const eventTaskId = streamEventTaskId(event)
          const eventConversationId = streamEventConversationId(event)

          if (eventTaskId) {
            streamedTaskId = eventTaskId
          }
          if (eventConversationId) {
            streamedConversationId = eventConversationId
          }

          if (eventType === 'meta' || eventType === 'node' || eventType === 'state') {
            setSnapshotPatch(streamedConversationId, {
              activeTaskId: streamedTaskId,
              taskStatus: 'GENERATING',
              loading: true,
              error: undefined
            })
            return
          }

          if (event.type === 'token') {
            if (!event.delta) return
            streamedText += event.delta
            ensureStreamAssistant()
            syncStreamAssistant()
            setSnapshotPatch(streamedConversationId, {
              activeTaskId: streamedTaskId,
              taskStatus: 'GENERATING',
              finalResult: streamedText,
              loading: true,
              error: undefined
            })
            return
          }

          if (event.type === 'awaiting_confirmation') {
            streamReachedTerminal = true
            setSnapshotPatch(streamedConversationId, {
              activeTaskId: streamedTaskId,
              taskStatus: 'PENDING_CONFIRM',
              subQuestions: normalizeSubQuestions(event.sub_questions),
              finalResult: '',
              loading: false,
              error: undefined
            })
            return
          }

          if (event.type === 'final') {
            streamReachedTerminal = true
            const finalAnswer = normalizeText(event.final_answer) || streamedText
            if (finalAnswer) {
              streamedText = finalAnswer
              if (streamMessageTime !== undefined) {
                syncStreamAssistant()
              } else {
                appendAssistantMessage(chatId, finalAnswer, streamedConversationId, streamedTaskId)
              }
            }

            setSnapshotPatch(streamedConversationId, {
              activeTaskId: streamedTaskId,
              taskStatus: 'DONE',
              subQuestions: [],
              finalResult: finalAnswer,
              loading: false,
              error: undefined
            })
            return
          }

          if (event.type === 'error') {
            streamReachedTerminal = true
            setSnapshotPatch(streamedConversationId, {
              activeTaskId: streamedTaskId,
              taskStatus: 'ERROR',
              loading: false,
              error: toErrorMessage(event.message, CONFIRM_FAILED_MESSAGE)
            })
          }
        },
        streamAbort.signal
      )

      if (!isStreamActive(streamRunId)) {
        return true
      }

      if (!streamReachedTerminal && normalizeText(streamedText)) {
        if (streamMessageTime !== undefined) {
          syncStreamAssistant()
        } else {
          appendAssistantMessage(chatId, streamedText, streamedConversationId, streamedTaskId)
        }
        setSnapshotPatch(streamedConversationId, {
          activeTaskId: streamedTaskId,
          taskStatus: 'DONE',
          subQuestions: [],
          finalResult: streamedText,
          loading: false,
          error: undefined
        })
      }

      return true
    }
    catch (streamError) {
      if (!streamAbort.signal.aborted && isStreamActive(streamRunId)) {
        setSnapshotPatch(conversationId, {
          activeTaskId: taskId,
          loading: false,
          taskStatus: 'ERROR',
          error: toErrorMessage(streamError, CONFIRM_FAILED_MESSAGE)
        })
      }
      return true
    }
    finally {
      if (streamAbortControllerRef.current === streamAbort) {
        streamAbortControllerRef.current = null
      }
      confirmInFlightRef.current = false
    }
  }, [
    appendAssistantMessage,
    chatState.subQuestions,
    chatState.taskId,
    chatState.taskStatus,
    modelName,
    modelUrl,
    beginStream,
    isStreamActive,
    setSnapshotPatch,
    stopActiveStream
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

    stopActiveStream()

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
            loading: false,
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
        activeTaskId: taskConversationId,
        taskStatus: 'GENERATING',
        loading: true,
        error: undefined,
        subQuestions: [],
        finalResult: ''
      })

      const {
        runId: streamRunId,
        controller: streamAbort
      } = beginStream()

      let streamedTaskId : string|undefined = taskConversationId
      let streamedConversationId = taskConversationId
      let streamedText = ''
      let streamMessageTime : number|undefined
      let streamReachedTerminal = false

      const ensureStreamAssistant = () => {
        if (streamMessageTime !== undefined) return
        const draft = createAssistantMessage('', streamedConversationId, streamedTaskId)
        streamMessageTime = draft.time
        disChats(addMessage({
          index: chatId,
          message: draft
        }))
      }

      const syncStreamAssistant = () => {
        if (streamMessageTime === undefined) return
        disChats(updateMessageContent({
          index: chatId,
          time: streamMessageTime,
          content: streamedText,
          conversationId: streamedConversationId,
          sessionId: streamedTaskId
        }))
      }

      try {
        await streamChatRequester(
          modelUrl,
          {
            model: modelName,
            stream: true,
            conversation_id: taskConversationId,
            session_id: taskConversationId,
            messages: [{ role: 'user', content: text }]
          },
          (event : ChatStreamEvent) => {
            if (!isStreamActive(streamRunId)) {
              return
            }

            const eventType = streamEventType(event)
            const eventTaskId = streamEventTaskId(event)
            const eventConversationId = streamEventConversationId(event)

            if (eventTaskId) {
              streamedTaskId = eventTaskId
            }
            if (eventConversationId) {
              streamedConversationId = eventConversationId
            }

            if (eventType === 'meta' || eventType === 'node' || eventType === 'state') {
              setSnapshotPatch(streamedConversationId, {
                activeTaskId: streamedTaskId,
                taskStatus: 'GENERATING',
                loading: true,
                error: undefined
              })
              return
            }

            if (event.type === 'token') {
              if (!event.delta) return
              streamedText += event.delta
              ensureStreamAssistant()
              syncStreamAssistant()
              setSnapshotPatch(streamedConversationId, {
                activeTaskId: streamedTaskId,
                taskStatus: 'GENERATING',
                finalResult: streamedText,
                loading: true,
                error: undefined
              })
              return
            }

            if (event.type === 'awaiting_confirmation') {
              streamReachedTerminal = true
              setSnapshotPatch(streamedConversationId, {
                activeTaskId: streamedTaskId,
                taskStatus: 'PENDING_CONFIRM',
                subQuestions: normalizeSubQuestions(event.sub_questions),
                finalResult: streamedText,
                loading: false,
                error: undefined
              })
              return
            }

            if (event.type === 'final') {
              streamReachedTerminal = true
              const finalAnswer = normalizeText(event.final_answer) || streamedText
              if (finalAnswer) {
                streamedText = finalAnswer
                if (streamMessageTime !== undefined) {
                  syncStreamAssistant()
                } else {
                  appendAssistantMessage(chatId, finalAnswer, streamedConversationId, streamedTaskId)
                }
              }
              setSnapshotPatch(streamedConversationId, {
                activeTaskId: streamedTaskId,
                taskStatus: 'DONE',
                subQuestions: [],
                finalResult: finalAnswer,
                loading: false,
                error: undefined
              })
              return
            }

            if (event.type === 'error') {
              streamReachedTerminal = true
              setSnapshotPatch(streamedConversationId, {
                activeTaskId: streamedTaskId,
                taskStatus: 'ERROR',
                loading: false,
                error: toErrorMessage(event.message, REQUEST_FAILED_MESSAGE)
              })
            }
          },
          streamAbort.signal
        )

        if (!isStreamActive(streamRunId)) {
          return
        }

        if (!streamReachedTerminal && normalizeText(streamedText)) {
          if (streamMessageTime !== undefined) {
            syncStreamAssistant()
          } else {
            appendAssistantMessage(chatId, streamedText, streamedConversationId, streamedTaskId)
          }
          setSnapshotPatch(streamedConversationId, {
            activeTaskId: streamedTaskId,
            taskStatus: 'DONE',
            subQuestions: [],
            finalResult: streamedText,
            loading: false,
            error: undefined
          })
        }
      }
      catch (streamError) {
        if (!streamAbort.signal.aborted && isStreamActive(streamRunId)) {
          setSnapshotPatch(taskConversationId, {
            activeTaskId: streamedTaskId,
            loading: false,
            taskStatus: 'ERROR',
            error: toErrorMessage(streamError)
          })
        }
      }
      finally {
        if (streamAbortControllerRef.current === streamAbort) {
          streamAbortControllerRef.current = null
        }
      }
    })()
  }, [
    appendAssistantMessage,
    chatState.taskId,
    chatState.taskStatus,
    modelName,
    modelUrl,
    beginStream,
    isStreamActive,
    setConversationIdForChat,
    setSnapshotPatch,
    stopActiveStream,
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
    stopActiveStream()
  }, [chat, stopActiveStream])

  useEffect(() => {
    const chatId = getChatIndex()
    if (chatId < 0 || chatId > chatsRef.current.length) {
      navigate(ROUTES.ROOT)
      return
    }

    currentChatIdRef.current = chatId
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
      return
    }

    setChatState((prev) => ({
      ...prev,
      currentChatId: chatId,
      conversationId: resolvedConversationId,
      taskId: resolvedConversationId,
      taskStatus: 'IDLE',
      subQuestions: [],
      finalResult: '',
      loading: false,
      error: undefined
    }))
  }, [chat, chats.length, applySnapshot, getConversationIdForChat, navigate, setConversationIdForChat])

  useEffect(() => {
    scroller(rowContainerRef, 1)
  }, [chats, chatState.currentChatId])

  useEffect(() => {
    return () => {
      stopActiveStream()
    }
  }, [stopActiveStream])

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

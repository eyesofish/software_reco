import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'

import ROUTES from '~/constants/routes'
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
import { useAppLanguage } from '~/services/language'
import About from '~/components/about'
import Button from '~/components/button'
import { ColumnContainer, RowContainer } from '~/components/containers'
import Menu from '~/components/menu'
import TextArea from '~/components/textarea'
import { InputContainer, Loading, Talk } from './style'
import LOADING from '~/assets/images/loading.png'
import './index.css'

const CONFIRM_ONLY_PATTERN = /^(?:\u786e\u8ba4|\u7ee7\u7eed|\u7ee7\u7eed\u5427|\u597d\u7684|\u597d|ok|okay|yes|y|go on|continue)[.!?\u3002\uff01\uff1f]*$/i
const RECOVERY_POLL_INTERVAL_MS = 2000
const RECOVERY_POLL_TIMEOUT_MS = 90_000
const WAITING_ACK_PATTERN = /waiting\s+for\s+confirmation|\u5f85\u786e\u8ba4|\u7b49\u5f85\u786e\u8ba4|\u786e\u8ba4\u540e\u7ee7\u7eed/i
const PENDING_LINE_PATTERN = /^\s*\d+[.)\u3001]\s*(.+?)\s*$/
const TASK_ID_STORAGE_PREFIX = 'reco_task_id_chat_'

export default function Chat () {
  const navigate = useNavigate()
  const { chat } = useParams()
  const [currentChatId, setCurrentChatId] = useState<number|null>(null)
  const [loading, setLoading] = useState(true)
  const [isAwaitingConfirmation, setIsAwaitingConfirmation] = useState(false)
  const [pendingSubQuestions, setPendingSubQuestions] = useState<string[]>([])
  const [pendingSessionId, setPendingSessionId] = useState<string|undefined>(undefined)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const chats = useChats('chats')
  const { autoSaveChats, modelName, modelUrl } = useConfig('config')
  const language = useAppLanguage()
  const activeConversationId = useRef<string|undefined>(undefined)
  const activeSessionId = useRef<string|undefined>(undefined)
  const shouldRestoreHistoryOnEnter = useRef(true)
  const renderCount = useRef(0)
  const rowContainerRef = useRef<HTMLDivElement>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const chatsRef = useRef(chats)
  const currentChatIdRef = useRef<number|null>(null)
  const recoveryAttemptRef = useRef(0)

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
    if (currentChatId !== chatId) return
    cancelRecoveryPolling()
    setCurrentChatId(null)
    shouldRestoreHistoryOnEnter.current = false
    activeConversationId.current = undefined
    activeSessionId.current = undefined
    setIsAwaitingConfirmation(false)
    setPendingSubQuestions([])
    setPendingSessionId(undefined)
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
      setIsAwaitingConfirmation(true)
      setPendingSubQuestions(hitl.pending)
      setPendingSessionId(sessionId)
      return
    }

    setIsAwaitingConfirmation(false)
    setPendingSubQuestions([])
    setPendingSessionId(undefined)
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
  ) {
    try {
      const taskState = await getRecommendTaskState(modelUrl, taskId)
      const status = String(taskState.status || '').trim().toUpperCase()
      const finalResult = String(taskState.final_result ?? taskState.finalResult ?? '').trim()
      const subQuestions = normalizePendingSubQuestions(
        taskState.sub_questions ?? taskState.subQuestions
      )
      const resolvedConversationId = conversationId || activeConversationId.current || taskId

      if (finalResult && !hasAnyDuplicateMessage(chatId, 'assistant', finalResult)) {
        appendAssistantMessage(finalResult, resolvedConversationId, taskId)
      }

      if (status === 'PENDING_CONFIRM') {
        setIsAwaitingConfirmation(true)
        setPendingSubQuestions(subQuestions)
        setPendingSessionId(taskId)
      } else {
        setIsAwaitingConfirmation(false)
        setPendingSubQuestions([])
        setPendingSessionId(undefined)
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
    } catch (error) {
      console.warn('task-state-restore-failed', {
        task_id: taskId,
        error
      })
    }
  }

  async function recoverMessagesByPolling (
    chatId : number,
    sessionId : string,
    conversationId ?: string
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
          shouldStop: () => (
            recoveryAttemptRef.current !== localAttemptId
            || currentChatIdRef.current !== chatId
          )
        }
      )

      if (result.status === 'timeout') {
        alert('Something went wrong :-(')
      }
    } catch (error) {
      alert('Something went wrong :-(')
      console.error(error)
    } finally {
      if (recoveryAttemptRef.current === localAttemptId) {
        setLoading(false)
      }
    }
  }

  function requestHandler () {
    if (loading || confirmLoading || currentChatId === null) return
    const messageText = getMessageText()?.trim() || ''
    if (!messageText) return

    if (isConfirmationOnlyText(messageText)) {
      clearTextArea()
      appendAssistantMessage(
        'Please click "Confirm and Continue" instead of sending "confirm/continue/ok" as a new question.',
        activeConversationId.current,
        activeSessionId.current
      )
      return
    }

    cancelRecoveryPolling()
    setLoading(true)
    shouldRestoreHistoryOnEnter.current = false
    setIsAwaitingConfirmation(false)
    setPendingSubQuestions([])
    setPendingSessionId(undefined)

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
      setIsAwaitingConfirmation(true)
      setPendingSubQuestions(pending)
      setPendingSessionId(sessionId)
      console.info('recommend-awaiting', {
        session_id: sessionId,
        status: response.status,
        pending_count: pending.length
      })
    } else {
      setIsAwaitingConfirmation(false)
      setPendingSubQuestions([])
      setPendingSessionId(undefined)
    }

    setLoading(false)
  }

  async function handleConfirmClicked () {
    if (confirmLoading || !pendingSessionId || currentChatId === null) return

    setConfirmLoading(true)
    const confirmPayload : ConfirmPayload = {
      session_id: pendingSessionId,
      action: 'confirm',
      sub_questions: [],
      comment: 'confirm'
    }

    try {
      console.info('confirm-clicked', {
        session_id: pendingSessionId
      })

      const confirmResponse = await confirmRequester(
        resolveConfirmUrl(modelUrl),
        confirmPayload
      )

      const conversationId = confirmResponse.conversation_id || activeConversationId.current
      const sessionId = confirmResponse.session_id || pendingSessionId
      const content = confirmResponse.message?.content || confirmResponse.final_answer || ''

      activeConversationId.current = conversationId
      activeSessionId.current = sessionId
      storeTaskId(currentChatIdRef.current, sessionId)

      if (content) {
        appendAssistantMessage(content, conversationId, sessionId)
      }

      if (confirmResponse.awaiting_human_confirmation) {
        const pending = confirmResponse.pending_sub_questions || []
        setIsAwaitingConfirmation(true)
        setPendingSubQuestions(pending)
        setPendingSessionId(sessionId)
        console.info('confirm-awaiting-again', {
          session_id: sessionId,
          status: confirmResponse.status,
          pending_count: pending.length
        })
      } else {
        setIsAwaitingConfirmation(false)
        setPendingSubQuestions([])
        setPendingSessionId(undefined)
        console.info('confirm-success', {
          session_id: sessionId,
          status: confirmResponse.status
        })
      }
    } catch (error) {
      alert('Confirm failed :-(')
      console.error(error)
    } finally {
      setConfirmLoading(false)
    }
  }

  function errorHandler (error : unknown) {
    console.error(error)
    const sessionId = activeSessionId.current?.trim()
    const chatId = currentChatIdRef.current

    if (!sessionId || chatId === null) {
      alert('Something went wrong :-(')
      setLoading(false)
      return
    }

    console.warn('recommend-request-failed-start-recovery', {
      session_id: sessionId,
      error
    })

    void recoverMessagesByPolling(chatId, sessionId, activeConversationId.current)
  }

  function getMessageText () {
    return textAreaRef.current?.value as string
  }

  function clearTextArea () {
    (textAreaRef.current as HTMLTextAreaElement).value = ''
    textAreaRef.current?.focus()
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
    // Wait until the chat slot is hydrated (e.g. AppBoot loads local chats asynchronously).
    if (!Array.isArray(currentMessages)) {
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
          if (restoreSessionId) {
            await restoreMessagesFromSessionState(currentChatId, restoreSessionId, restoreConversationId)
          }
          if (restoreTaskId) {
            await restoreStateFromTaskState(currentChatId, restoreTaskId, restoreConversationId)
          }
        })().finally(() => setLoading(false))
        return
      }

      setLoading(false)
      return
    }
  }, [chats, currentChatId, modelUrl, navigate])

  useEffect(() => {
    cancelRecoveryPolling()
    textAreaRef.current?.focus()
    shouldRestoreHistoryOnEnter.current = true
    setCurrentChatId(getChatIndex())
    activeConversationId.current = undefined
    activeSessionId.current = undefined
    setIsAwaitingConfirmation(false)
    setPendingSubQuestions([])
    setPendingSessionId(undefined)
    setConfirmLoading(false)
  }, [chat])

  const messages = currentChatId === null
    ? []
    : chats[currentChatId] || []
  const hasTalk = messages.length > 0

  return (
    <RowContainer ref={rowContainerRef}>
      <Menu loading={loading || confirmLoading} onDeleteChat={handleDeleteChat} scrollRef={rowContainerRef} />
      <ColumnContainer style={{ padding: 8, paddingRight: 0 }}>
        { (loading || confirmLoading) && <Loading src={LOADING} /> }
        { hasTalk
          ? (
            <Talk>
              {messages.map((message, messageIndex) => (
                message.role === 'assistant'
                  ? (
                    <div
                      className='assistantMessage'
                      key={`${message.time}-assistant-${messageIndex}`}
                    >
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>
                    )
                  : message.role === 'system'
                    ? (
                      <p className='systemMessage' key={`${message.time}-system-${messageIndex}`}>
                        {message.content}
                      </p>
                      )
                  : (
                    <p className='userMessage' key={`${message.time}-user-${messageIndex}`}>
                      {message.content}
                    </p>
                    )
              ))}
            </Talk>
            )
          : <About /> }

        {isAwaitingConfirmation && (
          <div className='hitlPanel'>
            <div className='hitlPanelTitle'>Generated sub-questions, please confirm to continue</div>
            <ul className='hitlQuestionList'>
              {pendingSubQuestions.map((question, idx) => (
                <li key={`${idx}-${question}`}>{question}</li>
              ))}
            </ul>
            <button
              className='hitlConfirmButton'
              disabled={confirmLoading || !pendingSessionId}
              onClick={handleConfirmClicked}
              type='button'
            >
              {confirmLoading ? 'Confirming...' : 'Confirm and Continue'}
            </button>
          </div>
        )}

        <InputContainer>
          <TextArea
            placeholder={language === 'zh' ? '\u6709\u95ee\u9898\uff0c\u5c3d\u7ba1\u95ee' : 'ask any questions'}
            ref={textAreaRef}
          />
          <Button onClick={requestHandler}>
            <svg width='24' height='24' viewBox='0 0 14 16'><path fill='currentColor' d='m6.2 0.9q0.2-0.2 0.4-0.2 0.2-0.1 0.4-0.1 0.2 0 0.4 0.1 0.2 0 0.4 0.2l5.2 5.1c0.2 0.3 0.3 0.6 0.3 0.9 0 0.2-0.2 0.5-0.4 0.7-0.2 0.3-0.5 0.4-0.8 0.4-0.3 0-0.5-0.1-0.8-0.3l-3.2-3.2v9.8c0 0.3-0.1 0.6-0.3 0.8-0.2 0.2-0.5 0.3-0.8 0.3-0.3 0-0.6-0.1-0.8-0.3-0.2-0.2-0.3-0.5-0.3-0.8v-9.8l-3.2 3.2c-0.2 0.2-0.5 0.3-0.8 0.3-0.4 0-0.7-0.1-0.9-0.3-0.2-0.2-0.3-0.5-0.3-0.8 0-0.3 0.1-0.6 0.3-0.9z' /></svg>
          </Button>
        </InputContainer>
      </ColumnContainer>
    </RowContainer>
  )
}

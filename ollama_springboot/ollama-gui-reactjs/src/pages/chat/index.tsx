import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'

import ROUTES from '~/constants/routes'
import { ConfirmPayload, Message, ModelResponse, SessionStateMessage } from '~/entities/messages'
import createAssistantMessage from '~/services/createAssistantMessage'
import createUserMessage from '~/services/createUserMessage'
import getChatIndex from '~/services/getChatIndex'
import requester, { confirmRequester, getSessionState, resolveConfirmUrl } from '~/services/requester'
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

const CONFIRM_ONLY_PATTERN = /^(?:确认|继续|继续吧|好的|好|ok|okay|yes|y|go on|continue)[.!?。！？]*$/i

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

  function handleDeleteChat (chatId : number) {
    if (currentChatId !== chatId) return
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

  function appendAssistantMessage (content : string, conversationId ?: string, sessionId ?: string) {
    if (!content || currentChatId === null) return
    const assistantMessage = createAssistantMessage(content, conversationId, sessionId)
    disChats(addMessage({ index: currentChatId, message: assistantMessage }))
  }

  function normalizeSessionMessages (messages : SessionStateMessage[]|undefined) : SessionStateMessage[] {
    if (!Array.isArray(messages)) {
      return []
    }

    const normalized : SessionStateMessage[] = []
    for (const item of messages as Array<Partial<SessionStateMessage>>) {
      const role = String(item.role || '').trim().toLowerCase()
      const content = String(item.content || '').trim()
      if (!content) continue
      if (role !== 'user' && role !== 'assistant' && role !== 'system') continue
      normalized.push({ role: role as SessionStateMessage['role'], content })
    }
    return normalized
  }

  async function restoreMessagesFromSessionState (
    chatId : number,
    sessionId : string,
    conversationId ?: string
  ) {
    try {
      const sessionState = await getSessionState(modelUrl, sessionId)
      const restoredSessionMessages = normalizeSessionMessages(sessionState.messages)
      if (restoredSessionMessages.length === 0) return

      const baseTime = Date.now()
      const restoredMessages : Message[] = restoredSessionMessages.map((message, index) => ({
        role: message.role,
        content: message.content,
        time: baseTime + index,
        conversationId: conversationId || sessionId,
        sessionId
      }))

      const nextChats = [...chats]
      nextChats[chatId] = restoredMessages
      disChats(loadChats(nextChats))

      activeConversationId.current = conversationId || sessionId
      activeSessionId.current = sessionId

      console.info('session-state-restored', {
        session_id: sessionId,
        restored_count: restoredMessages.length
      })
    } catch (error) {
      console.warn('session-state-restore-failed', {
        session_id: sessionId,
        error
      })
    }
  }

  function requestHandler () {
    if (loading || confirmLoading || currentChatId === null) return
    const messageText = getMessageText()?.trim() || ''
    if (!messageText) return

    if (isConfirmationOnlyText(messageText)) {
      clearTextArea()
      appendAssistantMessage(
        '请点击“确认并继续”按钮，不要把“确认/继续/ok”作为新问题发送。',
        activeConversationId.current,
        activeSessionId.current
      )
      return
    }

    setLoading(true)
    shouldRestoreHistoryOnEnter.current = false
    setIsAwaitingConfirmation(false)
    setPendingSubQuestions([])
    setPendingSessionId(undefined)

    const currentMessages = chats[currentChatId] || []
    const refs = resolveConversationRefs(currentMessages)
    activeConversationId.current = refs.conversationId
    activeSessionId.current = refs.sessionId

    const userMessage = createUserMessage(
      messageText,
      refs.conversationId,
      refs.sessionId
    )
    const messagesForRequest = [...currentMessages, userMessage]

    clearTextArea()
    disChats(addMessage({ index: currentChatId, message: userMessage }))

    requester(
      modelUrl,
      modelName,
      messagesForRequest,
      refs.conversationId || refs.sessionId,
      responseHandler,
      errorHandler
    )
  }

  function responseHandler (response : ModelResponse) {
    const conversationId = response.conversation_id || activeConversationId.current
    const sessionId = response.session_id || activeSessionId.current || conversationId
    const content = response.message?.content || response.final_answer || ''

    activeConversationId.current = conversationId
    activeSessionId.current = sessionId

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
    alert('Something went wrong :-(')
    console.error(error)
    setLoading(false)
  }

  function getMessageText () {
    return textAreaRef.current?.value as string
  }

  function clearTextArea () {
    (textAreaRef.current as HTMLTextAreaElement).value = ''
    textAreaRef.current?.focus()
  }

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
    scroller(rowContainerRef, 1)
    const refs = resolveConversationRefs(chats[currentChatId])
    activeConversationId.current = refs.conversationId
    activeSessionId.current = refs.sessionId

    if (shouldRestoreHistoryOnEnter.current && refs.sessionId) {
      shouldRestoreHistoryOnEnter.current = false
      setLoading(true)
      void restoreMessagesFromSessionState(currentChatId, refs.sessionId, refs.conversationId)
        .finally(() => setLoading(false))
      return
    }

    setLoading(false)
  }, [chats, currentChatId, modelUrl, navigate])

  useEffect(() => {
    textAreaRef.current?.focus()
    shouldRestoreHistoryOnEnter.current = true
    setCurrentChatId(getChatIndex())
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
            <div className='hitlPanelTitle'>已生成子问题，请确认后继续</div>
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
              {confirmLoading ? '确认中...' : '确认并继续'}
            </button>
          </div>
        )}

        <InputContainer>
          <TextArea
            placeholder={language === 'zh' ? '有问题，尽管问' : 'ask any questions'}
            ref={textAreaRef}
          />
          <Button onClick={requestHandler}>
            <svg width="24" height="24" viewBox="0 0 14 16"><path fill="currentColor" d="m6.2 0.9q0.2-0.2 0.4-0.2 0.2-0.1 0.4-0.1 0.2 0 0.4 0.1 0.2 0 0.4 0.2l5.2 5.1c0.2 0.3 0.3 0.6 0.3 0.9 0 0.2-0.2 0.5-0.4 0.7-0.2 0.3-0.5 0.4-0.8 0.4-0.3 0-0.5-0.1-0.8-0.3l-3.2-3.2v9.8c0 0.3-0.1 0.6-0.3 0.8-0.2 0.2-0.5 0.3-0.8 0.3-0.3 0-0.6-0.1-0.8-0.3-0.2-0.2-0.3-0.5-0.3-0.8v-9.8l-3.2 3.2c-0.2 0.2-0.5 0.3-0.8 0.3-0.4 0-0.7-0.1-0.9-0.3-0.2-0.2-0.3-0.5-0.3-0.8 0-0.3 0.1-0.6 0.3-0.9z"/></svg>
          </Button>
        </InputContainer>
      </ColumnContainer>
    </RowContainer>
  )
}

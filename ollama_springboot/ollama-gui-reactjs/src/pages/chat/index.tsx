import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'

import ROUTES from '~/constants/routes'
import { Message, ModelResponse } from '~/entities/messages'
import createAssistantMessage from '~/services/createAssistantMessage'
import createUserMessage from '~/services/createUserMessage'
import getChatIndex from '~/services/getChatIndex'
import requester from '~/services/requester'
import scroller from '~/services/scroller'
import Store from '~/services/store'
import { disChats, useChats } from '~/stores/chats'
import { addMessage } from '~/stores/chats/actions'
import { useConfig } from '~/stores/config'
import About from '~/components/about'
import Button from '~/components/button'
import { ColumnContainer, RowContainer } from '~/components/containers'
import Menu from '~/components/menu'
import TextArea from '~/components/textarea'
import { InputContainer, Loading, Talk } from './style'
import LOADING from '~/assets/images/loading.png'
import './index.css'


export default function Chat () {

  const navigate = useNavigate()
  const { chat } = useParams()
  const [index, setIndex] = useState<number|null>(null)
  const [loading, setLoading] = useState(true)
  const [talkMessages, setTalkMessages] = useState<Message[]>([])
  const [streamingAssistantContent, setStreamingAssistantContent] = useState('')
  const chats = useChats('chats')
  const { autoSaveChats, modelName, modelUrl } = useConfig('config')
  const assistantMessage = useRef<Message>(createAssistantMessage(''))
  const activeConversationId = useRef<string|undefined>(undefined)
  const activeSessionId = useRef<string|undefined>(undefined)
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

  function requestHandler () {
    if (loading) return
    const messageText = getMessageText()
    if (!messageText) return
    setLoading(true)
    setStreamingAssistantContent('')

    const currentMessages = chats[index as number] || []
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
    setTalkMessages(messages => [...messages, userMessage])
    disChats(addMessage({ index: index as number, message: userMessage }))
    assistantMessage.current = createAssistantMessage('')
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
    if (response.message.content) {
      assistantMessage.current.content += response.message.content
      setStreamingAssistantContent(content => content + response.message.content)
    }
    if (response.done) {
      const conversationId = response.conversation_id || activeConversationId.current
      const sessionId = response.session_id || activeSessionId.current
      const finalAssistantMessage = createAssistantMessage(
        assistantMessage.current.content,
        conversationId,
        sessionId
      )
      assistantMessage.current = finalAssistantMessage

      activeConversationId.current = conversationId
      activeSessionId.current = sessionId

      setTalkMessages(messages => [...messages, finalAssistantMessage])
      setStreamingAssistantContent('')
      disChats(addMessage({ index: index as number, message: finalAssistantMessage }))
      setLoading(false)
    }
  }

  function errorHandler (error : unknown) {
    alert('Something went wrong :-(')
    console.error(error)
    setStreamingAssistantContent('')
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
    if (index === null) {
      return
    }
    if (index < 0 || index > chats.length) {
      navigate(ROUTES.ROOT)
      return
    }
    scroller(rowContainerRef, 1)
    setStreamingAssistantContent('')
    setTalkMessages(chats[index] || [])
    const refs = resolveConversationRefs(chats[index])
    activeConversationId.current = refs.conversationId
    activeSessionId.current = refs.sessionId
    setLoading(false)
  }, [index])

  useEffect(() => {
    textAreaRef.current?.focus()
    setTalkMessages([])
    setStreamingAssistantContent('')
    setIndex(getChatIndex())
  }, [chat])

  const hasTalk = talkMessages.length > 0 || !!streamingAssistantContent

  return (
    <RowContainer ref={rowContainerRef}>
      <Menu loading={loading} scrollRef={rowContainerRef} />
      <ColumnContainer style={{ padding: 8, paddingRight: 0 }}>
        { loading && <Loading src={LOADING} /> }
        { hasTalk
          ? (
            <Talk>
              {talkMessages.map((message, messageIndex) => (
                message.role === 'assistant'
                  ? (
                    <div
                      className='assistantMessage'
                      key={`${message.time}-assistant-${messageIndex}`}
                    >
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>
                    )
                  : (
                    <p className='userMessage' key={`${message.time}-user-${messageIndex}`}>
                      {message.content}
                    </p>
                    )
              ))}
              { streamingAssistantContent && (
                <div className='assistantMessage assistantMessageStreaming'>
                  <ReactMarkdown>{streamingAssistantContent}</ReactMarkdown>
                </div>
              ) }
            </Talk>
            )
          : <About /> }
        <InputContainer>
          <TextArea
            placeholder='Message Ollama'
            ref={textAreaRef}
          />
          <Button onClick={requestHandler}>
            <svg width="24" height="24" viewBox="0 0 14 16"><path fill="#fff" d="m6.2 0.9q0.2-0.2 0.4-0.2 0.2-0.1 0.4-0.1 0.2 0 0.4 0.1 0.2 0 0.4 0.2l5.2 5.1c0.2 0.3 0.3 0.6 0.3 0.9 0 0.2-0.2 0.5-0.4 0.7-0.2 0.3-0.5 0.4-0.8 0.4-0.3 0-0.5-0.1-0.8-0.3l-3.2-3.2v9.8c0 0.3-0.1 0.6-0.3 0.8-0.2 0.2-0.5 0.3-0.8 0.3-0.3 0-0.6-0.1-0.8-0.3-0.2-0.2-0.3-0.5-0.3-0.8v-9.8l-3.2 3.2c-0.2 0.2-0.5 0.3-0.8 0.3-0.4 0-0.7-0.1-0.9-0.3-0.2-0.2-0.3-0.5-0.3-0.8 0-0.3 0.1-0.6 0.3-0.9z"/></svg>
          </Button>
        </InputContainer>
      </ColumnContainer>
    </RowContainer>
  )
}

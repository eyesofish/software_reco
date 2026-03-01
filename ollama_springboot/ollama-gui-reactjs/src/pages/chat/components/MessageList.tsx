import ReactMarkdown from 'react-markdown'

import { Message } from '~/entities/messages'
import { Talk } from '../style'

interface MessageListProps {
  messages : Message[]
}

export default function MessageList ({ messages } : MessageListProps) {
  return (
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
}

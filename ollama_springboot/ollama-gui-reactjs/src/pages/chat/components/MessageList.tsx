import ReactMarkdown from 'react-markdown'

import { Message } from '~/entities/messages'
import { Talk } from '../style'

interface MessageListProps {
  messages : Message[],
  apiBaseUrl : string
}

function resolveImageUrl (url : string, apiBaseUrl : string) {
  if (/^https?:\/\//i.test(url)) return url
  if (!url.startsWith('/')) return url
  return `${apiBaseUrl.replace(/\/+$/, '')}${url}`
}

export default function MessageList ({ messages, apiBaseUrl } : MessageListProps) {
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
              {message.retrievedImages && message.retrievedImages.length > 0 && (
                <div className='retrievedImageGrid'>
                  {message.retrievedImages.map((image) => (
                    <a
                      href={resolveImageUrl(image.url, apiBaseUrl)}
                      key={`${image.doc_id}-${image.url}`}
                      rel='noreferrer'
                      target='_blank'
                    >
                      <img alt={image.filename} src={resolveImageUrl(image.url, apiBaseUrl)} />
                      <strong>{image.filename}</strong>
                      {image.caption && <span>{image.caption}</span>}
                    </a>
                  ))}
                </div>
              )}
            </div>
            )
          : message.role === 'system'
            ? (
              <p className='systemMessage' key={`${message.time}-system-${messageIndex}`}>
                {message.content}
              </p>
              )
            : (
              <div className='userMessage' key={`${message.time}-user-${messageIndex}`}>
                <p>{message.content}</p>
                {message.images && message.images.length > 0 && (
                  <div className='userImageGrid'>
                    {message.images.map((image, imageIndex) => (
                      <img
                        alt={image.name}
                        key={`${image.name}-${imageIndex}`}
                        src={image.data_url}
                      />
                    ))}
                  </div>
                )}
              </div>
              )
      ))}
    </Talk>
  )
}

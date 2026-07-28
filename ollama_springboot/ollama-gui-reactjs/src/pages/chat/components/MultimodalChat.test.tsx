import '@testing-library/jest-dom'

import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'

import { ImageAttachment, Message } from '~/entities/messages'
import { AppThemeProvider } from '~/theme'
import ChatInput from './ChatInput'
import MessageList from './MessageList'

jest.mock('react-markdown', () => ({ children } : { children : string }) => <>{children}</>)

const image : ImageAttachment = {
  name: 'error.png',
  media_type: 'image/png',
  data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=='
}

function renderWithTheme (element : React.ReactElement) {
  return render(<AppThemeProvider>{element}</AppThemeProvider>)
}

test('shows selected image previews and removes them', () => {
  const onImageRemoved = jest.fn()

  renderWithTheme(
    <ChatInput
      images={[image]}
      language='en'
      onImageRemoved={onImageRemoved}
      onImagesSelected={jest.fn()}
      onSend={jest.fn()}
      textAreaRef={createRef<HTMLTextAreaElement>()}
    />
  )

  expect(screen.getByAltText('error.png')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Remove error.png' }))
  expect(onImageRemoved).toHaveBeenCalledWith(0)
})

test('renders user attachments and retrieved image evidence', () => {
  const messages : Message[] = [
    {
      role: 'user',
      content: 'Why did this fail?',
      time: 1,
      images: [image]
    },
    {
      role: 'assistant',
      content: 'The database connection was refused.',
      time: 2,
      retrievedImages: [
        {
          doc_id: 'screens/error.png',
          filename: 'knowledge-error.png',
          media_type: 'image/png',
          url: '/api/v1/assets/screens/error.png',
          caption: 'A terminal showing connection refused.',
          score: 0.1
        }
      ]
    }
  ]

  renderWithTheme(<MessageList apiBaseUrl='http://localhost:8080' messages={messages} />)

  expect(screen.getByAltText('error.png')).toBeInTheDocument()
  expect(screen.getByAltText('knowledge-error.png')).toHaveAttribute(
    'src',
    'http://localhost:8080/api/v1/assets/screens/error.png'
  )
  expect(screen.getByText('A terminal showing connection refused.')).toBeInTheDocument()
})

import '@testing-library/jest-dom'

import { createRef } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { setSession } from '~/services/auth'

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

test('renders user attachments and authenticated retrieved image evidence', async () => {
  setSession({ access_token: 'test-token', token_type: 'Bearer', expires_in: 1800, user: { id: 'user-a', username: 'alice' } })
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(['image']) })
  URL.createObjectURL = jest.fn().mockReturnValue('blob:knowledge-image')
  URL.revokeObjectURL = jest.fn()
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

  const mounted = renderWithTheme(<MessageList apiBaseUrl='http://localhost:8080' messages={messages} />)

  expect(screen.getByAltText('error.png')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByAltText('knowledge-error.png')).toHaveAttribute(
    'src',
    'blob:knowledge-image'
  ))
  expect(screen.getByText('A terminal showing connection refused.')).toBeInTheDocument()
  expect((fetch as jest.Mock).mock.calls[0][1].headers.get('Authorization')).toBe('Bearer test-token')
  mounted.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:knowledge-image')
  setSession(null)
})

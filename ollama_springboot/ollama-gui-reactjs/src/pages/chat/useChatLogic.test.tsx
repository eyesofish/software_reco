import '@testing-library/jest-dom'
import { webcrypto } from 'crypto'
import { TextEncoder } from 'util'
import { act, render, waitFor } from '@testing-library/react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ChatStreamEvent, ImageAttachment } from '~/entities/messages'
import { disChats, useChats } from '~/stores/chats'
import { loadChats } from '~/stores/chats/actions'
import { setSession } from '~/services/auth'
import * as requester from '~/services/requester'
import useChatLogic from './useChatLogic'

jest.mock('~/services/requester', () => ({
  ...jest.requireActual('~/services/requester'),
  streamChatRequester: jest.fn(), streamChatConfirmRequester: jest.fn(),
  listConversationTasksRequester: jest.fn()
}))
jest.mock('~/services/scroller', () => () => undefined)
jest.mock('~/stores/config', () => ({ useConfig: () => ({ autoSaveChats: false, modelUrl: 'http://localhost:8080', modelName: 'test' }) }))

let logic : ReturnType<typeof useChatLogic>
let storedChats : ReturnType<typeof useChats<'chats'>>
const image : ImageAttachment = { name: 'chart.png', media_type: 'image/png', data_url: 'data:image/png;base64,AA==' }

function Harness () {
  logic = useChatLogic()
  storedChats = useChats('chats')
  return <textarea ref={logic.textAreaRef} />
}

function mount () {
  return render(<BrowserRouter><Routes><Route path='/ollama-gui-reactjs/chat/:chat' element={<Harness />} /></Routes></BrowserRouter>)
}

function readyEvents (onEvent : (event : ChatStreamEvent) => void, conversation = 'old', task = 'task-1') {
  onEvent({ type: 'meta', conversation_id: conversation, task_id: task, status: 'GENERATING' })
  onEvent({ type: 'final', conversation_id: conversation, task_id: task, status: 'success', final_answer: 'Answer', retrieved_doc_ids: [], retrieved_images: [] })
}

beforeEach(() => {
  jest.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  Object.defineProperty(global, 'crypto', { configurable: true, value: webcrypto })
  global.TextEncoder = TextEncoder as typeof global.TextEncoder
  setSession({ access_token: 'test', token_type: 'Bearer', expires_in: 1800, user: { id: 'user-a', username: 'alice' } })
  window.history.replaceState({}, '', '/ollama-gui-reactjs/chat/1')
  act(() => disChats(loadChats([[{ role: 'user', content: 'Old question', time: 1, conversationId: 'old' }]])))
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([])
})
afterEach(() => { setSession(null) })

test('routing clarification retains attachments and retries one user message in the selected conversation', async () => {
  ;(requester.streamChatRequester as jest.Mock)
    .mockImplementationOnce(async (_url, _payload, emit) => {
      emit({ type: 'routing_clarification', clarification: { clarification_id: 'r1', kind: 'routing', prompt: 'Choose', options: [{ kind: 'existing', conversation_id: 'old', title: 'Current' }] } })
    })
    .mockImplementationOnce(async (_url, _payload, emit) => readyEvents(emit))
  mount()
  act(() => { logic.setRoutingMode('auto'); logic.handleImagesSelected([image]) })
  act(() => { logic.textAreaRef.current!.value = 'Explain this'; logic.requestHandler() })
  await waitFor(() => expect(logic.routingClarification?.clarification_id).toBe('r1'))
  expect(logic.loading).toBe(false)
  expect(logic.taskId).toBeUndefined()
  expect(storedChats[0]).toHaveLength(1)
  expect(logic.messages[1].images).toEqual([image])
  const firstPayload = (requester.streamChatRequester as jest.Mock).mock.calls[0][1]
  expect(firstPayload).toMatchObject({ routing_mode: 'auto', current_conversation_id: 'old', images: [image] })
  expect(firstPayload).not.toHaveProperty('session_id')
  await act(async () => { await logic.handleRoutingChoice({ kind: 'existing', conversation_id: 'old', title: 'Current' }, '') })
  expect(storedChats[0].filter((message) => message.role === 'user' && message.content === 'Explain this')).toHaveLength(1)
  expect((requester.streamChatRequester as jest.Mock).mock.calls[1][1]).toMatchObject({ conversation_id: 'old', routing_mode: 'explicit', images: [image] })
  expect(logic.taskStatus).toBe('DONE')
})

test('automatic new conversation creates a new local chat without rebinding old history', async () => {
  ;(requester.streamChatRequester as jest.Mock).mockImplementation(async (_url, _payload, emit) => readyEvents(emit, 'new-conversation'))
  mount()
  act(() => logic.setRoutingMode('auto'))
  act(() => { logic.textAreaRef.current!.value = 'Different topic'; logic.requestHandler() })
  await waitFor(() => expect(storedChats).toHaveLength(2))
  expect(storedChats[0]).toEqual([{ role: 'user', content: 'Old question', time: 1, conversationId: 'old' }])
  expect(storedChats[1][0]).toMatchObject({ content: 'Different topic', conversationId: 'new-conversation' })
  expect(logic.messages[0].content).toBe('Different topic')
  expect(window.location.pathname).toBe('/ollama-gui-reactjs/chat/2')
})

test('streaming continues after automatic navigation into a newly created conversation', async () => {
  let release : (() => void)|undefined
  let capturedSignal : AbortSignal|undefined
  ;(requester.streamChatRequester as jest.Mock).mockImplementation(async (_url, _payload, emit, signal) => {
    capturedSignal = signal
    emit({ type: 'meta', conversation_id: 'new-conversation', task_id: 'task-new', status: 'GENERATING' })
    await new Promise<void>((resolve) => { release = resolve })
    emit({ type: 'final', conversation_id: 'new-conversation', task_id: 'task-new', status: 'success', final_answer: 'Later answer', retrieved_doc_ids: [], retrieved_images: [] })
  })
  mount()
  act(() => logic.setRoutingMode('auto'))
  act(() => { logic.textAreaRef.current!.value = 'New subject'; logic.requestHandler() })
  await waitFor(() => expect(window.location.pathname).toBe('/ollama-gui-reactjs/chat/2'))
  expect(capturedSignal?.aborted).toBe(false)
  await act(async () => { release!() })
  expect(logic.taskStatus).toBe('DONE')
  expect(storedChats[1][1].content).toBe('Later answer')
})

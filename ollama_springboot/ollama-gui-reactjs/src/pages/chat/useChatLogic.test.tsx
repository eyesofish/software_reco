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
  streamChatRequester: jest.fn(), streamChatConfirmRequester: jest.fn(), streamClarifyRequester: jest.fn(),
  listConversationTasksRequester: jest.fn(), cancelWaitingTaskRequester: jest.fn()
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

test('manual retry after a lost response reuses its request ID, while a later identical send gets a new one', async () => {
  const send = requester.streamChatRequester as jest.Mock
  send.mockRejectedValueOnce(new Error('Connection lost'))
    .mockImplementationOnce(async (_url, _body, emit) => readyEvents(emit, 'old', 'task-1'))
    .mockImplementationOnce(async (_url, _body, emit) => readyEvents(emit, 'old', 'task-2'))
  mount()
  act(() => { logic.textAreaRef.current!.value = 'Same text'; logic.requestHandler() })
  await waitFor(() => expect(logic.error).toBe('Connection lost'))
  expect(logic.textAreaRef.current!.value).toBe('Same text')
  act(() => logic.requestHandler())
  await waitFor(() => expect(logic.taskStatus).toBe('DONE'))
  act(() => { logic.textAreaRef.current!.value = 'Same text'; logic.requestHandler() })
  await waitFor(() => expect(logic.taskId).toBe('task-2'))
  const ids = send.mock.calls.map(call => call[4])
  expect(ids[0]).toMatch(/^[0-9a-f-]{36}$/)
  expect(ids[1]).toBe(ids[0])
  expect(ids[2]).not.toBe(ids[0])
})

test('retry after a partial stream restores the existing local message and assistant reply', async () => {
  const send = requester.streamChatRequester as jest.Mock
  send.mockImplementationOnce(async (_url, _body, emit) => {
    emit({ type: 'meta', conversation_id: 'old', task_id: 'original', status: 'GENERATING' })
    emit({ type: 'token', conversation_id: 'old', task_id: 'original', delta: 'Par' })
    throw new Error('Connection lost')
  }).mockImplementationOnce(async (_url, _body, emit) => readyEvents(emit, 'old', 'original'))
  mount()
  act(() => { logic.textAreaRef.current!.value = 'Recover'; logic.requestHandler() })
  await waitFor(() => expect(logic.error).toBe('Connection lost'))
  act(() => { logic.textAreaRef.current!.value = 'Recover'; logic.requestHandler() })
  await waitFor(() => expect(logic.taskStatus).toBe('DONE'))
  expect(send.mock.calls[0][4]).toEqual(send.mock.calls[1][4])
  expect(storedChats[0].filter(message => message.sessionId === 'original').map(message => message.content)).toEqual(['Recover', 'Answer'])
})

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

test('runtime clarification blocks a new task and resumes the exact task and interruption', async () => {
  ;(requester.streamChatRequester as jest.Mock).mockImplementation(async (_url, _payload, emit) => {
    emit({ type: 'meta', conversation_id: 'old', task_id: 'task-1', status: 'GENERATING' })
    emit({ type: 'awaiting_input', conversation_id: 'old', task_id: 'task-1', pending_input: { clarification_id: 'input-1', kind: 'execution', prompt: 'Which platform?' } })
  })
  ;(requester.streamClarifyRequester as jest.Mock).mockImplementation(async (_url, _payload, emit) => readyEvents(emit))
  mount()
  act(() => { logic.textAreaRef.current!.value = 'Design it'; logic.requestHandler() })
  await waitFor(() => expect(logic.taskStatus).toBe('PENDING_INPUT'))
  act(() => { logic.textAreaRef.current!.value = 'Another task'; logic.requestHandler() })
  expect(requester.streamChatRequester).toHaveBeenCalledTimes(1)
  await act(async () => { await logic.handleInputSubmit('Linux') })
  expect((requester.streamClarifyRequester as jest.Mock).mock.calls[0][1]).toEqual({ task_id: 'task-1', clarification_id: 'input-1', answer: 'Linux' })
  expect(logic.taskStatus).toBe('DONE')
  expect(logic.taskId).toBe('task-1')
})

test('reload restores waiting input from server task state', async () => {
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([{ task_id: 'task-2', status: 'PENDING_INPUT', pending_input: { clarification_id: 'input-2', prompt: 'More detail' } }])
  mount()
  await waitFor(() => expect(logic.pendingInput?.clarification_id).toBe('input-2'))
  expect(logic.taskId).toBe('task-2')
  expect(logic.loading).toBe(false)
})

test('a failed resume keeps the waiting task available for retry or cancellation', async () => {
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([{ task_id: 'task-2', status: 'PENDING_INPUT', pending_input: { clarification_id: 'input-2', prompt: 'More detail' } }])
  ;(requester.streamClarifyRequester as jest.Mock).mockRejectedValue(new Error('Network unavailable'))
  ;(requester.cancelWaitingTaskRequester as jest.Mock).mockResolvedValue({ task_id: 'task-2', status: 'CANCELLED' })
  mount()
  await waitFor(() => expect(logic.pendingInput?.clarification_id).toBe('input-2'))
  await act(async () => { await logic.handleInputSubmit('Linux') })
  expect(logic.taskStatus).toBe('PENDING_INPUT')
  expect(logic.pendingInput?.clarification_id).toBe('input-2')
  expect(logic.error).toBe('Network unavailable')
  await act(async () => { await logic.handleCancelWaiting() })
  expect(requester.cancelWaitingTaskRequester).toHaveBeenCalledWith('http://localhost:8080', 'task-2')
  expect(logic.taskStatus).toBe('CANCELLED')
  expect(logic.pendingInput).toBeUndefined()
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

test('upstream rejection before resume acceptance preserves the form and retries one answer', async () => {
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([{ task_id: 'task-2', status: 'PENDING_INPUT', pending_input: { clarification_id: 'input-2', prompt: 'More detail' } }])
  ;(requester.streamClarifyRequester as jest.Mock)
    .mockImplementationOnce(async (_url, _payload, emit) => emit({ type: 'error', conversation_id: 'old', task_id: 'task-2', message: 'Agent unavailable' }))
    .mockImplementationOnce(async (_url, _payload, emit) => readyEvents(emit, 'old', 'task-2'))
  mount()
  await waitFor(() => expect(logic.pendingInput?.clarification_id).toBe('input-2'))
  await act(async () => { await logic.handleInputSubmit('Linux') })
  expect(logic.taskStatus).toBe('PENDING_INPUT')
  expect(logic.pendingInput?.clarification_id).toBe('input-2')
  expect(logic.error).toBe('Agent unavailable')
  expect(storedChats[0]).toHaveLength(1)
  act(() => { logic.textAreaRef.current!.value = 'Another task'; logic.requestHandler() })
  expect(requester.streamChatRequester).not.toHaveBeenCalled()
  await act(async () => { await logic.handleInputSubmit('Linux') })
  expect(logic.taskStatus).toBe('DONE')
  expect(storedChats[0].filter((message) => message.role === 'user' && message.content === 'Linux')).toHaveLength(1)
})

test('clarification limit is checked before the pending task is changed', async () => {
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([{ task_id: 'task-2', status: 'PENDING_INPUT', pending_input: { clarification_id: 'input-2', prompt: 'More detail' } }])
  mount()
  await waitFor(() => expect(logic.pendingInput?.clarification_id).toBe('input-2'))
  await act(async () => { await logic.handleInputSubmit('x'.repeat(4001)) })
  expect(requester.streamClarifyRequester).not.toHaveBeenCalled()
  expect(logic.taskStatus).toBe('PENDING_INPUT')
  expect(logic.error).toContain('4000')
  expect(storedChats[0]).toHaveLength(1)
})

test('failure after resume acceptance does not offer to replay consumed clarification', async () => {
  ;(requester.listConversationTasksRequester as jest.Mock).mockResolvedValue([{ task_id: 'task-2', status: 'PENDING_INPUT', pending_input: { clarification_id: 'input-2', prompt: 'More detail' } }])
  ;(requester.streamClarifyRequester as jest.Mock).mockImplementation(async (_url, _payload, emit) => {
    emit({ type: 'meta', conversation_id: 'old', task_id: 'task-2', status: 'GENERATING' })
    emit({ type: 'error', message: 'Generation failed' })
  })
  mount()
  await waitFor(() => expect(logic.pendingInput?.clarification_id).toBe('input-2'))
  await act(async () => { await logic.handleInputSubmit('Linux') })
  expect(logic.taskStatus).toBe('ERROR')
  expect(logic.pendingInput).toBeUndefined()
  expect(storedChats[0].filter((message) => message.role === 'user' && message.content === 'Linux')).toHaveLength(1)
})

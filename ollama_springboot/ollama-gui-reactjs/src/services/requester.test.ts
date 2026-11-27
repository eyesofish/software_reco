import { streamChatRequester, streamRecommendRequester, streamClarifyRequester } from './requester'
import { TextDecoder, TextEncoder } from 'util'
import { setSession } from './auth'

beforeEach(() => setSession({ access_token: 'test-token', token_type: 'Bearer', expires_in: 1800, user: { id: 'user-a', username: 'alice' } }))
afterEach(() => setSession(null))

test('a running replay follows the existing task and sends the request key only on the original POST', async () => {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder
  const frame = new TextEncoder().encode('event: meta\ndata: {"type":"meta","task_id":"existing","conversation_id":"old","status":"GENERATING","replayed":true}\n\n')
  global.fetch = jest.fn()
    .mockResolvedValueOnce({ ok: true, body: { getReader: () => ({
      read: jest.fn().mockResolvedValueOnce({ done: false, value: frame }).mockResolvedValueOnce({ done: true })
    }) } })
    .mockResolvedValueOnce({ ok: true, json: async () => ({
      task_id: 'existing', conversation_id: 'old', status: 'DONE', final_result: 'Stored answer',
      result_metadata: { retrieved_doc_ids: ['doc-a'], retrieved_images: [{ doc_id: 'doc-a', url: '/api/v1/assets/doc-a' }] }
    }) })
  const emit = jest.fn()
  await streamChatRequester('http://localhost:8080', { messages: [{ role: 'user', content: 'Hello' }] }, emit, undefined,
    '0cec1817-867d-4186-82b8-d114351d891f')
  const calls = (fetch as jest.Mock).mock.calls
  expect(calls).toHaveLength(2)
  expect(calls[0][1].headers.get('X-Request-Id')).toBe('0cec1817-867d-4186-82b8-d114351d891f')
  expect(calls[1][0]).toBe('http://localhost:8080/api/v1/tasks/existing')
  expect(emit).toHaveBeenLastCalledWith(expect.objectContaining({
    type: 'final', final_answer: 'Stored answer', task_id: 'existing', retrieved_doc_ids: ['doc-a'],
    retrieved_images: [expect.objectContaining({ doc_id: 'doc-a', url: '/api/v1/assets/doc-a' })]
  }))
})

describe('recommendation stream failures', () => {
  it('preserves run stop metadata from SSE error events', async () => {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder
    const eventPayload = {
      type: 'error',
      message: 'Request stopped: timeout.',
      stop_reason: 'timeout',
      run_id: 'run-123',
      elapsed_ms: 1000
    }
    const frame = new TextEncoder().encode(
      `event: error\ndata: ${JSON.stringify(eventPayload)}\n\n`
    )
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: jest.fn()
            .mockResolvedValueOnce({ done: false, value: frame })
            .mockResolvedValueOnce({ done: true, value: undefined })
        })
      }
    } as unknown as Response)
    const events: unknown[] = []

    await streamRecommendRequester(
      'http://localhost:8080',
      { query: 'recommend' },
      (event) => events.push(event)
    )

    expect(events[0]).toMatchObject({
      type: 'error',
      message: 'Request stopped: timeout.',
      stop_reason: 'timeout',
      run_id: 'run-123',
      elapsed_ms: 1000
    })
  })
})

test('preserves terminal routing clarification without manufacturing a task', async () => {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder
  const frame = new TextEncoder().encode(`event: routing_clarification\ndata: ${JSON.stringify({
    clarification: {
      clarification_id: 'route-1', prompt: 'Which conversation?',
      options: [{ kind: 'existing', conversation_id: 'a', title: 'Redis' }, { kind: 'new', title: 'New' }]
    }
  })}\n\n`)
  global.fetch = jest.fn().mockResolvedValue({ ok: true, body: { getReader: () => ({
    read: jest.fn().mockResolvedValueOnce({ done: false, value: frame }).mockResolvedValueOnce({ done: true })
  }) } })
  const events : unknown[] = []
  await streamRecommendRequester('http://localhost:8080', { query: 'continue', routing_mode: 'auto' }, (event) => events.push(event))
  expect(events[0]).toMatchObject({ type: 'routing_clarification', clarification: { clarification_id: 'route-1', options: [{ conversation_id: 'a' }, { kind: 'new' }] } })
  expect(events[0]).not.toHaveProperty('task_id')
})

test('runtime clarification keeps its task and uses the dedicated resume endpoint', async () => {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder
  const frame = new TextEncoder().encode(`event: awaiting_input\ndata: ${JSON.stringify({
    task_id: 'task-1', conversation_id: 'a', pending_input: { clarification_id: 'input-2', prompt: 'Which platform?' }
  })}\n\n`)
  global.fetch = jest.fn().mockResolvedValue({ ok: true, body: { getReader: () => ({
    read: jest.fn().mockResolvedValueOnce({ done: false, value: frame }).mockResolvedValueOnce({ done: true })
  }) } })
  const events : unknown[] = []
  await streamClarifyRequester('http://localhost:8080', { task_id: 'task-1', clarification_id: 'input-1', answer: 'Linux' }, (event) => events.push(event))
  expect((fetch as jest.Mock).mock.calls[0][0]).toBe('http://localhost:8080/api/v1/recommend/clarify/stream')
  expect(events[0]).toMatchObject({ type: 'awaiting_input', task_id: 'task-1', pending_input: { clarification_id: 'input-2', kind: 'execution' } })
})

import { streamRecommendRequester } from './requester'
import { TextDecoder, TextEncoder } from 'util'

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
      'http://localhost:8000',
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

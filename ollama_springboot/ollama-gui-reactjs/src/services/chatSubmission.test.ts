import { webcrypto } from 'crypto'
import { TextEncoder } from 'util'
import { setSession } from './auth'
import { prepareChatSubmission, pendingSubmissionMode } from './chatSubmission'
import { ChatStreamRequest } from './requester'

const account = (user : string) => ({ access_token: user, token_type: 'Bearer', expires_in: 3600, user: { id: user, username: user } })

test('tab storage restores the same key and routing choice across reload without saving chat contents or sharing across users', async () => {
  sessionStorage.clear()
  Object.defineProperty(global, 'crypto', { configurable: true, value: webcrypto })
  global.TextEncoder = TextEncoder as typeof global.TextEncoder
  const payload : ChatStreamRequest = { model: 'chat', stream: true, messages: [{ role: 'user', content: 'Private example' }],
    images: [{ name: 'note.png', data_url: 'data:image/png;base64,AAAA', media_type: 'image/png' }] }
  const explicit = { routing_mode: 'explicit' as const, conversation_id: 'chosen', session_id: 'chosen' }
  const auto = { routing_mode: 'auto' as const, current_conversation_id: 'source' }
  try {
    setSession(account('alice'))
    const first = await prepareChatSubmission(0, 'source', 'server', 'auto', payload, explicit)
    first.rememberDestination('chosen')
    expect(sessionStorage.getItem('reco:alice:chat-send:source')).not.toMatch(/Private example|note\.png|base64/)
    expect(pendingSubmissionMode(0, 'source')).toBe('auto')
    // HTTP deployments without SubtleCrypto must recover a digest produced by the native implementation.
    Object.defineProperty(global, 'crypto', { configurable: true, value: { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) } })
    const retry = await prepareChatSubmission(0, 'source', 'server', 'auto', payload, auto)
    expect(retry.id).toBe(first.id)
    expect(retry.route).toEqual(explicit)
    setSession(account('bob'))
    const other = await prepareChatSubmission(0, 'source', 'server', 'auto', payload, auto)
    expect(other.id).not.toBe(first.id)
    other.finish()
    retry.finish()
    expect(sessionStorage.getItem('reco:alice:chat-send:chosen')).toBeNull()
  } finally {
    Object.defineProperty(global, 'crypto', { configurable: true, value: webcrypto })
    setSession(null)
  }
})

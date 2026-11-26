import { API_BASE, authenticatedFetch, getSession, setSession, validateSession } from './auth'
import Store from './store'
import { streamChatRequester } from './requester'
import { TextDecoder } from 'util'

const alice = { access_token: 'alice-token', token_type: 'Bearer', expires_in: 1800, user: { id: 'user-a', username: 'alice' } }
const bob = { ...alice, access_token: 'bob-token', user: { id: 'user-b', username: 'bob' } }

beforeEach(() => { setSession(null); localStorage.clear(); global.fetch = jest.fn() })
afterEach(() => setSession(null))

test('JWT stays in sessionStorage and chat caches are isolated without importing anonymous history', () => {
  localStorage.setItem('chats', JSON.stringify([[{ content: 'legacy secret' }]]))
  setSession(alice)
  expect(Store.get('chats')).toBeNull()
  Store.set('chats', [[{ content: 'A secret' }]])
  setSession(bob)
  expect(Store.get('chats')).toBeNull()
  Store.set('chats', [[{ content: 'B secret' }]])
  setSession(alice)
  expect(Store.get('chats')[0][0].content).toBe('A secret')
  Store.clear()
  expect(localStorage.getItem('chats')).toContain('legacy secret')
  expect(localStorage.getItem('reco:user-b:chats')).toContain('B secret')
  expect(sessionStorage.getItem('reco_auth_session')).toContain('alice-token')
  expect(localStorage.getItem('reco_auth_session')).toBeNull()
  setSession(null)
  expect(Store.get('chats')).toBeNull()
})

test('authenticated requests keep other headers and never send JWT to a different server', async () => {
  setSession(alice)
  ;(fetch as jest.Mock).mockResolvedValue({ ok: true, status: 200 })
  await authenticatedFetch(`${API_BASE}/api/conversations`, { headers: { Accept: 'application/json', Authorization: 'wrong' } })
  const headers = (fetch as jest.Mock).mock.calls[0][1].headers as Headers
  expect(headers.get('Authorization')).toBe('Bearer alice-token')
  expect(headers.get('Accept')).toBe('application/json')
  await expect(authenticatedFetch('https://other.example/api/chat')).rejects.toThrow('configured business server')
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('401 clears the session and aborts other requests', async () => {
  setSession(alice)
  let signal : AbortSignal
  ;(fetch as jest.Mock).mockImplementation((_url, init) => {
    signal = init.signal
    return Promise.resolve({ status: 401 })
  })
  await expect(authenticatedFetch(`${API_BASE}/api/conversations`)).rejects.toThrow('Session expired')
  expect(getSession()).toBeNull()
  expect(signal!.aborted).toBe(true)
})

test('late failures from an old user cannot invalidate a new login', async () => {
  setSession(alice)
  let finish : (value : unknown) => void
  ;(fetch as jest.Mock).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const pending = authenticatedFetch(`${API_BASE}/api/conversations`)
  setSession(bob)
  finish!({ status: 401 })
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  expect(getSession()?.user.id).toBe('user-b')
})

test('me validates the restored identity before chat hydration', async () => {
  setSession(alice)
  ;(fetch as jest.Mock).mockResolvedValue({ ok: true, status: 200, json: async () => alice.user })
  expect(await validateSession()).toBe(true)
  ;(fetch as jest.Mock).mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 'tampered-id' }) })
  expect(await validateSession()).toBe(false)
  expect(getSession()).toBeNull()
  setSession(alice)
  ;(fetch as jest.Mock).mockResolvedValue({ status: 401 })
  expect(await validateSession()).toBe(false)
  expect(getSession()).toBeNull()
})

test('switching users cancels an active SSE body reader', async () => {
  global.TextDecoder = TextDecoder as typeof global.TextDecoder
  setSession(alice)
  let reading : () => void
  const ready = new Promise<void>(resolve => { reading = resolve })
  ;(fetch as jest.Mock).mockImplementation((_url, init) => Promise.resolve({
    ok: true, status: 200,
    body: { getReader: () => ({ read: () => {
      reading()
      return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError'))))
    } }) }
  }))
  const stream = streamChatRequester(API_BASE, { messages: [{ role: 'user', content: 'hello' }] }, jest.fn())
  await ready
  setSession(bob)
  await expect(stream).rejects.toMatchObject({ name: 'AbortError' })
  expect(getSession()?.user.id).toBe('user-b')
})

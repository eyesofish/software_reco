import { DEFAULT_CONFIG } from '~/config/defaults'

export interface AuthUser { id : string, username : string }
export interface AuthSession {
  access_token : string,
  token_type : string,
  expires_in : number,
  user : AuthUser
}

export const API_BASE = DEFAULT_CONFIG.modelUrl.replace(/\/api\/chat\/?$/, '').replace(/\/+$/, '')
const SESSION_KEY = 'reco_auth_session'
const listeners = new Set<() => void>()
let session : AuthSession|null = restoreSession()
let sessionController = new AbortController()

function restoreSession () : AuthSession|null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null')
    return value && typeof value.access_token === 'string' && typeof value.user?.id === 'string'
      && typeof value.user?.username === 'string' ? value : null
  } catch { return null }
}

export function getSession () { return session }
export function subscribeAuth (listener : () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function setSession (value : AuthSession|null) {
  sessionController.abort()
  sessionController = new AbortController()
  session = value
  if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value))
  else sessionStorage.removeItem(SESSION_KEY)
  listeners.forEach(listener => listener())
}

export function userStorageKey (key : string) {
  return session ? `reco:${session.user.id}:${key}` : undefined
}

function combineSignals (other ?: AbortSignal|null) {
  const signals = other ? [other, sessionController.signal] : [sessionController.signal]
  const factory = AbortSignal as typeof AbortSignal & { any ?: (signals : AbortSignal[]) => AbortSignal }
  if (factory.any) return factory.any(signals)
  const combined = new AbortController()
  const abort = () => {
    combined.abort()
    signals.forEach(signal => signal.removeEventListener('abort', abort))
  }
  signals.forEach(signal => {
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  })
  return combined.signal
}

export async function authenticatedFetch (url : string, init : RequestInit = {}) {
  const active = session
  if (!active) throw new Error('Please sign in')
  const target = new URL(url, API_BASE)
  const base = new URL(API_BASE)
  if (target.origin !== base.origin || !target.pathname.startsWith(`${base.pathname.replace(/\/$/, '')}/api/`)) {
    throw new Error('Authenticated requests must use the configured business server')
  }
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${active.access_token}`)
  const response = await fetch(target.href, { ...init, headers, signal: combineSignals(init.signal) })
  if (session !== active) throw new DOMException('Session changed', 'AbortError')
  if (response.status === 401) {
    setSession(null)
    throw new Error('Session expired. Please sign in again.')
  }
  return response
}

export async function login (username : string, password : string) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  })
  if (!response.ok) throw new Error(response.status === 401 ? 'Invalid username or password' : 'Sign-in failed')
  const value = await response.json() as AuthSession
  setSession(value)
  return value
}

export async function validateSession () {
  const active = session
  if (!active) return false
  try {
    const response = await authenticatedFetch(`${API_BASE}/api/auth/me`)
    if (!response.ok) throw new Error('Could not verify session')
    const user = await response.json() as AuthUser
    if (session !== active) return false
    if (user.id !== active.user.id) {
      setSession(null)
      return false
    }
    return true
  } catch {
    if (session === active) setSession(null)
    return false
  }
}

import { userStorageKey } from './auth'
import { ChatStreamRequest } from './requester'
import { routingFields } from './routing'
import { RoutingMode } from '~/entities/messages'

interface Submission {
  id : string,
  signature : string,
  route : ReturnType<typeof routingFields>,
  selectedMode : RoutingMode,
  slots : string[]
}

// Keep each tab's unacknowledged sends separate. Persist only the digest, IDs and routing fields.
const pending = new Map<string, Submission>()

function slot (prefix : string|undefined, chatId : number, conversationId ?: string) {
  return prefix && prefix + (conversationId || `new-${chatId}`)
}

function read (key : string|undefined) : Submission|undefined {
  if (!key) return undefined
  const cached = pending.get(key)
  if (cached) return cached
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (value && typeof value.id === 'string' && typeof value.signature === 'string'
      && Array.isArray(value.slots) && value.route) return value
  } catch { /* Storage may be disabled. */ }
  return undefined
}

function save (key : string|undefined, value : Submission) {
  if (!key) return
  if (!value.slots.includes(key)) value.slots.push(key)
  try {
    sessionStorage.setItem(key, JSON.stringify(value))
    pending.delete(key)
  } catch { pending.set(key, value) }
}

export function pendingSubmissionMode (chatId : number, origin ?: string) {
  return read(slot(userStorageKey('chat-send:'), chatId, origin))?.selectedMode
}

function newId () {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}

export async function prepareChatSubmission (
  chatId : number, origin : string|undefined, modelUrl : string, selectedMode : RoutingMode,
  payload : ChatStreamRequest, route : ReturnType<typeof routingFields>
) {
  const prefix = userStorageKey('chat-send:')
  const input = JSON.stringify([modelUrl, payload.model, selectedMode, payload.messages, payload.images])
  let signature : string
  if (crypto.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input))
    signature = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  } else {
    // Web Crypto digest requires HTTPS or localhost. Plain HTTP uses the same digest.
    const { sha256 } = await import('js-sha256')
    signature = sha256(input)
  }
  const key = slot(prefix, chatId, origin)
  const previous = read(key)
  const value = previous?.signature === signature ? previous : { id: newId(), route, selectedMode, signature, slots: [] }
  save(key, value)
  return {
    id: value.id,
    route: value.route,
    rememberDestination: (destination : string) => {
      const target = slot(prefix, chatId, destination)
      if (target && !value.slots.includes(target)) {
        save(target, value)
        save(key, value)
      }
    },
    finish: () => {
      for (const entry of value.slots) {
        if (read(entry)?.id !== value.id) continue
        pending.delete(entry)
        try { sessionStorage.removeItem(entry) } catch { /* Storage may be disabled. */ }
      }
    }
  }
}

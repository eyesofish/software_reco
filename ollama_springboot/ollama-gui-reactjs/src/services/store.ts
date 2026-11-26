import { userStorageKey } from '~/services/auth'
const KEYS = {
  chats: true,
  config: true
}

export default class Store {

  static clear () {
    const key = userStorageKey('chats')
    const ids = userStorageKey('reco_chat_conversation_ids')
    if (key) localStorage.removeItem(key)
    if (ids) localStorage.removeItem(ids)
  }

  static get (key : keyof typeof KEYS) {
    if (!KEYS[key]) throw new Error(`Key not allowed: ${key}`)
    const storageKey = key === 'chats' ? userStorageKey(key) : key
    if (!storageKey) return null
    const value = localStorage.getItem(storageKey)
    if (!value) return null
    try {
      return JSON.parse(value)
    } catch {
      return null
    }
  }

  static set (key : keyof typeof KEYS, value : any) {
    if (!KEYS[key]) throw new Error(`Key not allowed: ${key}`)
    const storageKey = key === 'chats' ? userStorageKey(key) : key
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify(value))
  }

}

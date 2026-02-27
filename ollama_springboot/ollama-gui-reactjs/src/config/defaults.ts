const FALLBACK_MODEL_URL = 'http://localhost:8080/api/chat'

function isValidUrl (value: string | null | undefined) {
  if (!value) return false
  try {
    // URL constructor throws on invalid urls
    new URL(value)
    return true
  } catch {
    return false
  }
}

const envModelUrl = process.env.REACT_APP_CHAT_API_URL?.trim()
const resolvedModelUrl = isValidUrl(envModelUrl) ? envModelUrl as string : FALLBACK_MODEL_URL

export const DEFAULT_CONFIG = {
  autoSaveChats: true,
  modelName: 'llama3',
  modelUrl: resolvedModelUrl
}

export type AppConfig = typeof DEFAULT_CONFIG

export function sanitizeConfig (raw: unknown): { config: AppConfig, changed: boolean } {
  let changed = false
  const config: AppConfig = { ...DEFAULT_CONFIG }

  if (raw && typeof raw === 'object') {
    const maybe = raw as Partial<AppConfig>

    if (typeof maybe.autoSaveChats === 'boolean') {
      config.autoSaveChats = maybe.autoSaveChats
    } else {
      changed = true
    }

    if (typeof maybe.modelName === 'string' && maybe.modelName.trim()) {
      config.modelName = maybe.modelName
    } else {
      changed = true
    }

    if (typeof maybe.modelUrl === 'string' && isValidUrl(maybe.modelUrl)) {
      config.modelUrl = maybe.modelUrl
    } else {
      changed = true
    }
  } else {
    changed = true
  }

  return { config, changed }
}

export function getDefaultConfig (): AppConfig {
  return { ...DEFAULT_CONFIG }
}

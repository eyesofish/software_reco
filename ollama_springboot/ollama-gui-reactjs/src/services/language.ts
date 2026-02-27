import { useEffect, useState } from 'react'

export type AppLanguage = 'en' | 'zh'

const LANGUAGE_STORAGE_KEY = 'language'
const LANGUAGE_CHANGE_EVENT = 'app-language-change'

function normalizeLanguage (value: string | null): AppLanguage {
  return value === 'zh' ? 'zh' : 'en'
}

export const I18N = {
  en: {
    aboutDescription: 'This is a software recommendation system based on large language models',
    animationSpeed: 'Animation speed',
    chat: 'Chat',
    clearAll: 'Clear all',
    clearAllConfirm: 'Are you sure you want to delete everything?\nThis cannot be undone.',
    dark: 'Dark',
    enableSplashAnimation: 'Enable splash animation',
    english: 'English',
    language: 'Language',
    light: 'Light',
    fontSize: 'Font size',
    modelName: 'qwen',
    modelUrl: 'Model URL',
    modelNameNote: 'For record only; does not change the recommendation model.',
    modelUrlWarning: 'This URL is protected. Changing it incorrectly will break chat requests.',
    resetConfig: 'Reset config',
    configSource: 'Current config is loaded from localStorage',
    save: 'Save',
    saveAllChats: 'Save all chats',
    splashTitle: 'Software Recommendation System',
    theme: 'Theme',
    chinese: 'Chinese'
  },
  zh: {
    aboutDescription: '这是一个基于大语言模型的软件推荐系统',
    animationSpeed: '动画速度',
    chat: '聊天',
    clearAll: '清空全部',
    clearAllConfirm: '确定要删除所有内容吗？\n此操作无法撤销。',
    dark: '深色',
    enableSplashAnimation: '启用启动动画',
    english: '英文',
    language: '语言',
    light: '浅色',
    fontSize: '字体大小',
    modelName: '千问',
    modelUrl: '模型 URL',
    modelNameNote: '仅用于记录，不影响推荐模型选择。',
    modelUrlWarning: '此地址受保护，修改错误会导致无法对话。',
    resetConfig: '重置配置',
    configSource: '当前配置来源：localStorage',
    save: '保存',
    saveAllChats: '保存所有聊天',
    splashTitle: '软件推荐系统',
    theme: '主题',
    chinese: '中文'
  }
} as const

export function getStoredLanguage (): AppLanguage {
  return normalizeLanguage(localStorage.getItem(LANGUAGE_STORAGE_KEY))
}

export function setStoredLanguage (language: AppLanguage) {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  window.dispatchEvent(new Event(LANGUAGE_CHANGE_EVENT))
}

export function useAppLanguage () {
  const [language, setLanguage] = useState<AppLanguage>(() => getStoredLanguage())

  useEffect(() => {
    function syncLanguage () {
      setLanguage(getStoredLanguage())
    }

    function handleStorage (event: StorageEvent) {
      if (event.key !== LANGUAGE_STORAGE_KEY) return
      syncLanguage()
    }

    window.addEventListener(LANGUAGE_CHANGE_EVENT, syncLanguage)
    window.addEventListener('storage', handleStorage)

    return () => {
      window.removeEventListener(LANGUAGE_CHANGE_EVENT, syncLanguage)
      window.removeEventListener('storage', handleStorage)
    }
  }, [])

  return language
}

import { type ChangeEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import scroller from '~/services/scroller'
import Store from '~/services/store'
import { I18N, type AppLanguage, setStoredLanguage, useAppLanguage } from '~/services/language'
import { disConfig, useConfig } from '~/stores/config'
import { toggleAutoSaveChats, updateConfig } from '~/stores/config/actions'
import Button from '~/components/button'
import { ColumnContainer, Filler, RowContainer } from '~/components/containers'
import Footer from '~/components/footer'
import Menu from '~/components/menu'
import TextInput from '~/components/textInput'
import Toggle from '~/components/toggle'
import { ButtonsContainer, ThemeContainer, ThemeLabel, ThemeSelect } from './style'

type ThemeOption = 'dark' | 'light'


export default function Config () {

  const fillerRef = useRef({ height: '24px' })
  const renderCount = useRef(0)
  const rowContainerRef = useRef<HTMLDivElement>(null)
  const config = useConfig('config')
  const language = useAppLanguage()
  const text = I18N[language]
  const [modelName, setModelName] = useState(config.modelName)
  const [modelUrl, setModelUrl] = useState(config.modelUrl)
  const [theme, setTheme] = useState<ThemeOption>('dark')
  const [enableSplash, setEnableSplash] = useState(true)
  const [starSpeed, setStarSpeed] = useState(1)
  const [fontSize, setFontSize] = useState(16)
  const navigate = useNavigate()

  function applyFontSize (value: number) {
    const clamped = Math.min(20, Math.max(14, value))
    setFontSize(clamped)
    document.documentElement.style.setProperty('--app-font-size', `${clamped}px`)
  }

  function handleClearAll () {
    const confirmed = confirm(text.clearAllConfirm)
    if (!confirmed) return
    Store.clear()
    navigate(ROUTES.ROOT)
    window.location.reload()
  }

  function handleSave () {
    disConfig(updateConfig({
      ...config, modelName, modelUrl
    }))
    localStorage.setItem('theme', theme)
    document.documentElement.setAttribute('data-theme', theme)
    navigate('/')
  }

  function handleThemeChange (event: ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value as ThemeOption
    setTheme(value)
    localStorage.setItem('theme', value)
    document.documentElement.setAttribute('data-theme', value)
  }

  function handleLanguageChange (event: ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value as AppLanguage
    setStoredLanguage(value)
  }

  function handleEnableSplashChange () {
    const value = !enableSplash
    setEnableSplash(value)
    localStorage.setItem('enableSplash', String(value))
  }

  function handleStarSpeedChange (event: ChangeEvent<HTMLInputElement>) {
    const value = Number(event.target.value)
    setStarSpeed(value)
    localStorage.setItem('starSpeed', String(value))
  }

  function handleFontSizeChange (event: ChangeEvent<HTMLInputElement>) {
    const value = Number(event.target.value)
    applyFontSize(value)
    localStorage.setItem('fontSize', String(Math.min(20, Math.max(14, value))))
  }

  useEffect(() => {
    scroller(rowContainerRef, 1)
  }, [])

  useEffect(() => {
    const savedTheme = localStorage.getItem('theme')
    const initialTheme = savedTheme === 'light' ? 'light' : 'dark'
    setTheme(initialTheme)
    document.documentElement.setAttribute('data-theme', initialTheme)
  }, [])

  useEffect(() => {
    const savedEnableSplash = localStorage.getItem('enableSplash')
    if (savedEnableSplash === null) return
    setEnableSplash(savedEnableSplash === 'true')
  }, [])

  useEffect(() => {
    const savedStarSpeed = localStorage.getItem('starSpeed')
    if (savedStarSpeed === null) return
    const parsed = Number(savedStarSpeed)
    if (Number.isNaN(parsed)) return
    const clamped = Math.min(2, Math.max(0.1, parsed))
    setStarSpeed(clamped)
  }, [])

  useEffect(() => {
    const savedFontSize = localStorage.getItem('fontSize')
    if (savedFontSize === null) {
      applyFontSize(16)
      return
    }

    const parsed = Number(savedFontSize)
    if (Number.isNaN(parsed)) {
      applyFontSize(16)
      return
    }

    applyFontSize(parsed)
  }, [])

  useEffect(() => {
    renderCount.current++
    if (renderCount.current < 2) return
    Store.set('config', config)
  }, [config])

  return (
    <RowContainer ref={rowContainerRef}>
      <Menu scrollRef={rowContainerRef} />
      <ColumnContainer>
        <Filler />
        <Filler height='auto' width='88%'>
          <ThemeContainer>
            <ThemeLabel htmlFor='language'>{text.language}</ThemeLabel>
            <ThemeSelect id='language' value={language} onChange={handleLanguageChange}>
              <option value='en'>{text.english}</option>
              <option value='zh'>{text.chinese}</option>
            </ThemeSelect>
          </ThemeContainer>
          <Filler height={fillerRef.current.height} />
          <ThemeContainer>
            <ThemeLabel htmlFor='theme'>{text.theme}</ThemeLabel>
            <ThemeSelect id='theme' value={theme} onChange={handleThemeChange}>
              <option value='dark'>{text.dark}</option>
              <option value='light'>{text.light}</option>
            </ThemeSelect>
          </ThemeContainer>
          <Filler height={fillerRef.current.height} />
          <Toggle
            checked={enableSplash}
            id='enableSplash'
            label={text.enableSplashAnimation}
            onChange={handleEnableSplashChange}
          />
          <Filler height={fillerRef.current.height} />
          <ThemeContainer>
            <ThemeLabel htmlFor='starSpeed'>
              {text.animationSpeed} ({starSpeed.toFixed(1)}x)
            </ThemeLabel>
            <input
              id='starSpeed'
              max='2.0'
              min='0.1'
              onChange={handleStarSpeedChange}
              step='0.1'
              style={{ width: '100%' }}
              type='range'
              value={starSpeed}
            />
          </ThemeContainer>
          <Filler height={fillerRef.current.height} />
          <ThemeContainer>
            <ThemeLabel htmlFor='fontSize'>
              {text.fontSize} ({fontSize}px)
            </ThemeLabel>
            <input
              id='fontSize'
              max='20'
              min='14'
              onChange={handleFontSizeChange}
              step='1'
              style={{ width: '100%' }}
              type='range'
              value={fontSize}
            />
          </ThemeContainer>
          <Filler height={fillerRef.current.height} />
          <Toggle
            checked={config.autoSaveChats} id='autoSaveChats'
            label={text.saveAllChats} onChange={() => disConfig(toggleAutoSaveChats())}
          />
          <Filler height={fillerRef.current.height} />
          <TextInput
            placeholder={text.modelUrl} value={modelUrl}
            onChange={e => setModelUrl(e.target.value)}
          />
          <Filler height={fillerRef.current.height} />
          <TextInput
            placeholder={text.modelName} value={modelName}
            onChange={e => setModelName(e.target.value)}
          />
          <Filler height={fillerRef.current.height} />
          <ButtonsContainer>
            <Button onClick={handleClearAll}>{text.clearAll}</Button>
            <Button onClick={() => navigate(ROUTES.GO_BACK)}>{text.chat}</Button>
            <Button onClick={handleSave}>{text.save}</Button>
          </ButtonsContainer>
        </Filler>
        <Filler />
        <Footer />
      </ColumnContainer>
    </RowContainer>
  )

}

import { type ChangeEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import ROUTES from '~/constants/routes'
import scroller from '~/services/scroller'
import Store from '~/services/store'
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
  const [modelName, setModelName] = useState(config.modelName)
  const [modelUrl, setModelUrl] = useState(config.modelUrl)
  const [theme, setTheme] = useState<ThemeOption>('dark')
  const navigate = useNavigate()

  function handleClearAll () {
    const confirmed = confirm('Are you sure you want to delete everything?\nThis cannot be undone.')
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
            <ThemeLabel htmlFor='theme'>Theme</ThemeLabel>
            <ThemeSelect id='theme' value={theme} onChange={handleThemeChange}>
              <option value='dark'>Dark</option>
              <option value='light'>Light</option>
            </ThemeSelect>
          </ThemeContainer>
          <Filler height={fillerRef.current.height} />
          <Toggle
            checked={config.autoSaveChats} id='autoSaveChats'
            label='Save all chats' onChange={() => disConfig(toggleAutoSaveChats())}
          />
          <Filler height={fillerRef.current.height} />
          <TextInput
            placeholder='Model URL' value={modelUrl}
            onChange={e => setModelUrl(e.target.value)}
          />
          <Filler height={fillerRef.current.height} />
          <TextInput
            placeholder='Model Name' value={modelName}
            onChange={e => setModelName(e.target.value)}
          />
          <Filler height={fillerRef.current.height} />
          <ButtonsContainer>
            <Button onClick={handleClearAll}>Clear all</Button>
            <Button onClick={() => navigate(ROUTES.GO_BACK)}>Chat</Button>
            <Button onClick={handleSave}>Save</Button>
          </ButtonsContainer>
        </Filler>
        <Filler />
        <Footer />
      </ColumnContainer>
    </RowContainer>
  )

}

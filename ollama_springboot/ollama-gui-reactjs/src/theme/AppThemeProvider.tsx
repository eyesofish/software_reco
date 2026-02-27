import React, { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { ThemeProvider as StyledThemeProvider } from 'styled-components'

import { GlobalStyle } from './globalStyle'
import { AppTheme, ThemeName, themes } from './tokens'

interface ThemeContextValue {
  themeName: ThemeName
  setThemeName: (theme: ThemeName) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  themeName: 'dark',
  setThemeName: () => undefined
})

function resolveInitialTheme () : ThemeName {
  if (typeof window === 'undefined') return 'dark'
  const saved = localStorage.getItem('theme')
  return saved === 'light' ? 'light' : 'dark'
}

export function useAppTheme () {
  return useContext(ThemeContext)
}

export default function AppThemeProvider ({ children } : { children: React.ReactNode }) {
  const [themeName, setThemeName] = useState<ThemeName>(resolveInitialTheme)

  useEffect(() => {
    localStorage.setItem('theme', themeName)
    document.documentElement.setAttribute('data-theme', themeName)
  }, [themeName])

  const theme : AppTheme = useMemo(() => themes[themeName], [themeName])

  return (
    <ThemeContext.Provider value={{ themeName, setThemeName }}>
      <StyledThemeProvider theme={theme}>
        <GlobalStyle />
        {children}
      </StyledThemeProvider>
    </ThemeContext.Provider>
  )
}

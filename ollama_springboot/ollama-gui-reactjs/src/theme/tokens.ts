export type ThemeName = 'light' | 'dark'

export interface ThemeColors {
  background: string
  surface: string
  surfaceMuted: string
  textPrimary: string
  textSecondary: string
  border: string
  accent: string
  accentHover: string
  accentActive: string
  accentFocus: string
  placeholder: string
  codeBg: string
  codeText: string
  codeBorder: string
  userBubble: string
  assistantPreBg: string
  menuBg: string
  menuBorder: string
  menuText: string
  menuTextActive: string
  menuHoverBg: string
  menuShadow: string
  footerBg: string
  overlay: string
  inputBg: string
  inputBorder: string
  inputText: string
  inputPlaceholder: string
  textareaBg: string
  textareaBorder: string
  textareaText: string
  textareaPlaceholder: string
  toggleTrack: string
  toggleTrackChecked: string
  toggleKnob: string
  shadowLow: string
  shadowMedium: string
  shadowHigh: string
  scrollTrack: string
  scrollThumb: string
  scrollThumbHover: string
  hitlBg: string
  hitlBorder: string
  hitlText: string
  hitlButtonBg: string
  hitlButtonDisabledBg: string
  hitlButtonText: string
  splashBg: string
  splashOverlay: string
  splashText: string
  splashCanvasBg: string
  splashStar: string
  splashTextShadow: string
  link: string
  linkHover: string
  linkActive: string
}

export interface AppTheme {
  name: ThemeName
  colors: ThemeColors
}

export const lightTheme: AppTheme = {
  name: 'light',
  colors: {
    background: '#F4F4F5',
    surface: '#FFFFFF',
    surfaceMuted: '#F7F7FA',
    textPrimary: '#1C1C1E',
    textSecondary: '#4A4A4F',
    border: '#D1D5DB',
    accent: '#007AFF',
    accentHover: '#005BB5',
    accentActive: '#004A99',
    accentFocus: 'rgba(0, 122, 255, 0.3)',
    placeholder: 'rgba(28, 28, 30, 0.55)',
    codeBg: '#F3F4F6',
    codeText: '#1F2937',
    codeBorder: 'rgba(0, 0, 0, 0.08)',
    userBubble: 'rgba(0, 122, 255, 0.08)',
    assistantPreBg: 'rgba(0, 0, 0, 0.06)',
    menuBg: '#F8F9FB',
    menuBorder: 'rgba(15, 23, 42, 0.08)',
    menuText: '#3A3A3A',
    menuTextActive: '#0F172A',
    menuHoverBg: 'rgba(0, 122, 255, 0.12)',
    menuShadow: '0 6px 18px rgba(15, 23, 42, 0.12)',
    footerBg: 'rgba(0, 0, 0, 0.05)',
    overlay: 'rgba(0, 0, 0, 0.05)',
    inputBg: '#FFFFFF',
    inputBorder: '#C7C7CF',
    inputText: '#1C1C1E',
    inputPlaceholder: 'rgba(28, 28, 30, 0.55)',
    textareaBg: '#FFFFFF',
    textareaBorder: '#D0D5DD',
    textareaText: '#1C1C1E',
    textareaPlaceholder: 'rgba(28, 28, 30, 0.55)',
    toggleTrack: '#C7C7CF',
    toggleTrackChecked: '#007AFF',
    toggleKnob: '#FFFFFF',
    shadowLow: '0 1px 3px rgba(0, 0, 0, 0.1)',
    shadowMedium: '0 4px 12px rgba(0, 0, 0, 0.12)',
    shadowHigh: '0 6px 16px rgba(0, 0, 0, 0.14)',
    scrollTrack: '#E5E7EB',
    scrollThumb: 'rgba(17, 24, 39, 0.25)',
    scrollThumbHover: 'rgba(17, 24, 39, 0.38)',
    hitlBg: 'linear-gradient(135deg, #f2efe5 0%, #e9dcc7 100%)',
    hitlBorder: '#d6c6aa',
    hitlText: '#2f2617',
    hitlButtonBg: '#106f5a',
    hitlButtonDisabledBg: '#88a499',
    hitlButtonText: '#FFFFFF',
    splashBg: 'radial-gradient(circle at 15% 20%, #17203d 0%, #070b19 42%, #010206 100%)',
    splashOverlay: 'radial-gradient(circle at center, rgba(16, 27, 68, 0.18) 0%, rgba(0, 0, 0, 0.72) 82%)',
    splashText: '#f5f7ff',
    splashCanvasBg: '#030612',
    splashStar: '#FFFFFF',
    splashTextShadow: '0 0 24px rgba(163, 197, 255, 0.55), 0 0 46px rgba(120, 169, 255, 0.25)',
    link: '#007AFF',
    linkHover: '#005BB5',
    linkActive: '#004A99'
  }
}

export const darkTheme: AppTheme = {
  name: 'dark',
  colors: {
    background: '#1C1C1E',
    surface: '#26262A',
    surfaceMuted: '#2F3036',
    textPrimary: '#F8F8F8',
    textSecondary: 'rgba(248, 248, 248, 0.75)',
    border: 'rgba(255, 255, 255, 0.14)',
    accent: '#4DA3FF',
    accentHover: '#69B5FF',
    accentActive: '#3B82F6',
    accentFocus: 'rgba(77, 163, 255, 0.35)',
    placeholder: 'rgba(248, 248, 248, 0.65)',
    codeBg: 'rgba(255, 255, 255, 0.06)',
    codeText: '#E5E7EB',
    codeBorder: 'rgba(255, 255, 255, 0.1)',
    userBubble: 'rgba(255, 255, 255, 0.08)',
    assistantPreBg: 'rgba(255, 255, 255, 0.08)',
    menuBg: 'rgba(24, 24, 26, 0.8)',
    menuBorder: 'rgba(255, 255, 255, 0.08)',
    menuText: 'rgba(255, 255, 255, 0.75)',
    menuTextActive: '#FFFFFF',
    menuHoverBg: 'rgba(255, 255, 255, 0.08)',
    menuShadow: '0 6px 18px rgba(0, 0, 0, 0.35)',
    footerBg: 'rgba(255, 255, 255, 0.08)',
    overlay: 'rgba(0, 0, 0, 0.35)',
    inputBg: 'rgba(255, 255, 255, 0.08)',
    inputBorder: 'rgba(255, 255, 255, 0.25)',
    inputText: '#FFFFFF',
    inputPlaceholder: 'rgba(248, 248, 248, 0.65)',
    textareaBg: 'rgba(255, 255, 255, 0.08)',
    textareaBorder: 'rgba(255, 255, 255, 0.25)',
    textareaText: '#FFFFFF',
    textareaPlaceholder: 'rgba(248, 248, 248, 0.65)',
    toggleTrack: 'rgba(255, 255, 255, 0.25)',
    toggleTrackChecked: '#4DA3FF',
    toggleKnob: '#FFFFFF',
    shadowLow: '0 1px 3px rgba(0, 0, 0, 0.35)',
    shadowMedium: '0 4px 12px rgba(0, 0, 0, 0.45)',
    shadowHigh: '0 6px 16px rgba(0, 0, 0, 0.55)',
    scrollTrack: '#2B2B2E',
    scrollThumb: 'rgba(255, 255, 255, 0.25)',
    scrollThumbHover: 'rgba(255, 255, 255, 0.35)',
    hitlBg: 'linear-gradient(135deg, #1f2a38 0%, #151c26 100%)',
    hitlBorder: '#334155',
    hitlText: '#E2E8F0',
    hitlButtonBg: '#3B82F6',
    hitlButtonDisabledBg: 'rgba(255, 255, 255, 0.18)',
    hitlButtonText: '#FFFFFF',
    splashBg: 'radial-gradient(circle at 15% 20%, #0b1020 0%, #050912 42%, #010206 100%)',
    splashOverlay: 'radial-gradient(circle at center, rgba(16, 27, 68, 0.22) 0%, rgba(0, 0, 0, 0.8) 82%)',
    splashText: '#f5f7ff',
    splashCanvasBg: '#030612',
    splashStar: '#FFFFFF',
    splashTextShadow: '0 0 24px rgba(163, 197, 255, 0.45), 0 0 46px rgba(120, 169, 255, 0.2)',
    link: '#8BC4FF',
    linkHover: '#A3D1FF',
    linkActive: '#6FAEFF'
  }
}

export const themes: Record<ThemeName, AppTheme> = {
  light: lightTheme,
  dark: darkTheme
}

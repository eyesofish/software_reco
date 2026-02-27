import { createGlobalStyle } from 'styled-components'

export const GlobalStyle = createGlobalStyle`
  :root {
    --app-font-size: 16px;

    /* core */
    --color-bg: ${({ theme }) => theme.colors.background};
    --color-surface: ${({ theme }) => theme.colors.surface};
    --color-surface-muted: ${({ theme }) => theme.colors.surfaceMuted};
    --color-text: ${({ theme }) => theme.colors.textPrimary};
    --color-text-muted: ${({ theme }) => theme.colors.textSecondary};
    --color-border: ${({ theme }) => theme.colors.border};

    /* accent */
    --color-accent: ${({ theme }) => theme.colors.accent};
    --color-accent-hover: ${({ theme }) => theme.colors.accentHover};
    --color-accent-active: ${({ theme }) => theme.colors.accentActive};
    --color-accent-focus: ${({ theme }) => theme.colors.accentFocus};
    --color-link: ${({ theme }) => theme.colors.link};
    --color-link-hover: ${({ theme }) => theme.colors.linkHover};
    --color-link-active: ${({ theme }) => theme.colors.linkActive};

    /* form + text */
    --color-placeholder: ${({ theme }) => theme.colors.placeholder};
    --color-input-bg: ${({ theme }) => theme.colors.inputBg};
    --color-input-border: ${({ theme }) => theme.colors.inputBorder};
    --color-input-text: ${({ theme }) => theme.colors.inputText};
    --color-input-placeholder: ${({ theme }) => theme.colors.inputPlaceholder};
    --color-textarea-bg: ${({ theme }) => theme.colors.textareaBg};
    --color-textarea-border: ${({ theme }) => theme.colors.textareaBorder};
    --color-textarea-text: ${({ theme }) => theme.colors.textareaText};
    --color-textarea-placeholder: ${({ theme }) => theme.colors.textareaPlaceholder};
    --color-toggle-track: ${({ theme }) => theme.colors.toggleTrack};
    --color-toggle-track-checked: ${({ theme }) => theme.colors.toggleTrackChecked};
    --color-toggle-knob: ${({ theme }) => theme.colors.toggleKnob};

    /* chat / markdown */
    --color-user-bubble: ${({ theme }) => theme.colors.userBubble};
    --color-assistant-pre: ${({ theme }) => theme.colors.assistantPreBg};
    --color-code-border: ${({ theme }) => theme.colors.codeBorder};
    --color-hitl-bg: ${({ theme }) => theme.colors.hitlBg};
    --color-hitl-border: ${({ theme }) => theme.colors.hitlBorder};
    --color-hitl-text: ${({ theme }) => theme.colors.hitlText};
    --color-hitl-button-bg: ${({ theme }) => theme.colors.hitlButtonBg};
    --color-hitl-button-disabled: ${({ theme }) => theme.colors.hitlButtonDisabledBg};
    --color-hitl-button-text: ${({ theme }) => theme.colors.hitlButtonText};

    /* layout */
    --color-menu-bg: ${({ theme }) => theme.colors.menuBg};
    --color-menu-border: ${({ theme }) => theme.colors.menuBorder};
    --color-menu-text: ${({ theme }) => theme.colors.menuText};
    --color-menu-text-active: ${({ theme }) => theme.colors.menuTextActive};
    --color-menu-hover-bg: ${({ theme }) => theme.colors.menuHoverBg};
    --color-menu-shadow: ${({ theme }) => theme.colors.menuShadow};
    --color-footer-bg: ${({ theme }) => theme.colors.footerBg};
    --color-overlay: ${({ theme }) => theme.colors.overlay};

    /* splash */
    --color-splash-bg: ${({ theme }) => theme.colors.splashBg};
    --color-splash-overlay: ${({ theme }) => theme.colors.splashOverlay};
    --color-splash-text: ${({ theme }) => theme.colors.splashText};
    --color-splash-canvas: ${({ theme }) => theme.colors.splashCanvasBg};
    --color-splash-star: ${({ theme }) => theme.colors.splashStar};
    --color-splash-text-shadow: ${({ theme }) => theme.colors.splashTextShadow};

    /* shadows + scrollbar */
    --shadow-low: ${({ theme }) => theme.colors.shadowLow};
    --shadow-medium: ${({ theme }) => theme.colors.shadowMedium};
    --shadow-high: ${({ theme }) => theme.colors.shadowHigh};
    --scroll-track: ${({ theme }) => theme.colors.scrollTrack};
    --scroll-thumb: ${({ theme }) => theme.colors.scrollThumb};
    --scroll-thumb-hover: ${({ theme }) => theme.colors.scrollThumbHover};

    /* backward compatibility with previous custom properties */
    --config-input-bg-color: ${({ theme }) => theme.colors.inputBg};
    --config-input-border-color: ${({ theme }) => theme.colors.inputBorder};
    --config-input-placeholder-color: ${({ theme }) => theme.colors.inputPlaceholder};
    --config-input-text-color: ${({ theme }) => theme.colors.inputText};
    --config-toggle-label-color: ${({ theme }) => theme.colors.textPrimary};
    --text-color: ${({ theme }) => theme.colors.textPrimary};
    --bg-color: ${({ theme }) => theme.colors.background};
  }
`

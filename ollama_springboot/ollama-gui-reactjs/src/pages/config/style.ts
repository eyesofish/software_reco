import styled from 'styled-components'

export const ButtonsContainer = styled.div`
  display: flex;
  justify-content: space-between;
  width: 100%;
`

export const ThemeContainer = styled.div`
  text-align: left;
  width: 100%;
`

export const ThemeLabel = styled.label`
  display: block;
  margin-bottom: 8px;
`

export const ThemeSelect = styled.select`
  background-color: ${({ theme }) => theme.colors.menuBg};
  border: 1px solid ${({ theme }) => theme.colors.menuBorder};
  border-radius: 8px;
  box-shadow: ${({ theme }) => theme.colors.shadowLow};
  color: ${({ theme }) => theme.colors.menuText};
  appearance: none;
  outline: none;
  padding: 12px 16px;
  width: 100%;

  &:focus {
    border-color: ${({ theme }) => theme.colors.accent};
    box-shadow: 0 0 0 4px ${({ theme }) => theme.colors.accentFocus};
    color: ${({ theme }) => theme.colors.menuTextActive};
  }

  option {
    background: ${({ theme }) => theme.colors.menuBg};
    color: ${({ theme }) => theme.colors.menuTextActive};
  }

  option:checked,
  option:hover {
    background: ${({ theme }) => theme.colors.menuHoverBg};
    color: ${({ theme }) => theme.colors.menuTextActive};
  }
`

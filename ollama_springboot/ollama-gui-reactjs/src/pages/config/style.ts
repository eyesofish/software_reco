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
  background-color: ${({ theme }) => theme.colors.inputBg};
  border: 1px solid ${({ theme }) => theme.colors.inputBorder};
  border-radius: 8px;
  box-shadow: ${({ theme }) => theme.colors.shadowLow};
  color: ${({ theme }) => theme.colors.inputText};
  appearance: none;
  outline: none;
  padding: 12px 16px;
  width: 100%;

  &:focus {
    border-color: ${({ theme }) => theme.colors.accent};
    box-shadow: 0 0 0 4px ${({ theme }) => theme.colors.accentFocus};
  }

  option {
    background: ${({ theme }) => theme.colors.inputBg};
    color: ${({ theme }) => theme.colors.inputText};
  }
`

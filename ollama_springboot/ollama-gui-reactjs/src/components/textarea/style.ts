import styled from 'styled-components'

export const StyledTextArea = styled.textarea`
  background-color: ${({ theme }) => theme.colors.textareaBg};
  border: 1px solid ${({ theme }) => theme.colors.textareaBorder};
  border-radius: 8px;
  box-shadow: ${({ theme }) => theme.colors.shadowLow};
  color: ${({ theme }) => theme.colors.textareaText};
  margin-right: 8px;
  outline: none;
  overflow-y: hidden;
  padding: 12px 16px;
  resize: none;
  transition: background-color 0.3s, border-color 0.3s, box-shadow 0.3s, color 0.3s;
  width: 100%;

  &::placeholder {
    color: ${({ theme }) => theme.colors.textareaPlaceholder};
  }

  &:focus {
    border-color: ${({ theme }) => theme.colors.accent};
    box-shadow: 0 0 0 4px ${({ theme }) => theme.colors.accentFocus};
  }
`

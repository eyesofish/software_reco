import styled from 'styled-components'

export const StyledButton = styled.button`
  background-color: ${({ theme }) => theme.colors.accent};
  border: none;
  border-radius: 8px;
  box-shadow: ${({ theme }) => theme.colors.shadowMedium};
  color: ${({ theme }) => theme.colors.hitlButtonText};
  cursor: pointer;
  display: inline-block;
  font-weight: 500;
  padding: 12px 24px;
  transition: background-color 0.3s, box-shadow 0.3s;

  &:hover {
    background-color: ${({ theme }) => theme.colors.accentHover};
    box-shadow: ${({ theme }) => theme.colors.shadowHigh};
  }

  &:focus {
    box-shadow: 0 0 0 4px ${({ theme }) => theme.colors.accentFocus};
    outline: none;
  }

  &:active {
    background-color: ${({ theme }) => theme.colors.accentActive};
    box-shadow: ${({ theme }) => theme.colors.shadowMedium};
  }

  svg {
    color: inherit;
    pointer-events: none;
  }
`

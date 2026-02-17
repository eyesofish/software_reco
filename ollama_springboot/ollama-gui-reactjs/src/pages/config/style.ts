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
  background-color: rgba(255,255,255,0.25);
  border: 1px solid #dcdcdc;
  border-radius: 8px;
  color: inherit;
  outline: none;
  padding: 12px 16px;
  width: 100%;

  &:focus {
    border-color: #007aff;
    box-shadow: 0 0 0 4px rgba(0, 122, 255, 0.2);
  }
`

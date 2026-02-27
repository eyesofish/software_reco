import styled from 'styled-components'

export const Ball = styled.span<{ checked : boolean }>`
  background-color: ${({ theme }) => theme.colors.toggleKnob};
  border-radius: 50%;
  box-shadow: ${({ theme }) => theme.colors.shadowLow};
  height: 26px;
  left: 4px;
  position: absolute;
  top: 4px;
  transform: translateX(${({ checked }) => (checked ? '26px' : '0')});
  transition: transform 0.3s ease;
  width: 26px;
`

export const Checkbox = styled.input`
  display: none;
`

export const Container = styled.div`
  align-items: center;
  color: ${({ theme }) => theme.colors.textPrimary};
  display: flex;
  justify-content: space-between;
  transition: color 0.3s ease;
  width: 100%;
`

export const Label = styled.label<{ checked : boolean }>`
  background-color: ${({ checked, theme }) => (checked ? theme.colors.toggleTrackChecked : theme.colors.toggleTrack)};
  border-radius: 34px;
  cursor: pointer;
  display: block;
  height: 100%;
  position: relative;
  transition: background-color 0.3s ease;
  width: 100%;

  &:before {
    background-color: ${({ theme }) => theme.colors.surfaceMuted};
    border-radius: inherit;
    content: '';
    height: 100%;
    left: 50%;
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%) scale(${({ checked }) => (checked ? 0 : 1.1)});
    transition: transform 0.3s ease;
    width: 100%;
  }
`

export const ToggleContainer = styled.div`
  height: 34px;
  position: relative;
  width: 60px;
`

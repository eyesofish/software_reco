import React from 'react'

import { StyledButton } from './style'


interface ButtonProps {
  children : React.ReactNode,
  onClick ?: React.MouseEventHandler<HTMLButtonElement>,
  style ?: React.CSSProperties
}


export default function Button ({ children, onClick, style } : ButtonProps) {
  return (
    <StyledButton
      type='button'
      onClick={onClick}
      style={style}
    >
      { children }
    </StyledButton>
  )
}

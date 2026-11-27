import React from 'react'

import { StyledButton } from './style'


interface ButtonProps {
  children : React.ReactNode,
  onClick ?: React.MouseEventHandler<HTMLButtonElement>,
  style ?: React.CSSProperties,
  disabled ?: boolean,
  ariaLabel ?: string
}


export default function Button ({ children, onClick, style, disabled, ariaLabel } : ButtonProps) {
  return (
    <StyledButton
      type='button'
      onClick={onClick}
      style={style}
      disabled={disabled}
      aria-label={ariaLabel}
    >
      { children }
    </StyledButton>
  )
}

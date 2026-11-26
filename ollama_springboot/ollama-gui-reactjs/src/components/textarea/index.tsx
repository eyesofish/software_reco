import React, { forwardRef } from 'react'

import { StyledTextArea } from './style'


interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  placeholder : string
}


export default forwardRef<HTMLTextAreaElement, TextAreaProps>((props, ref) => {
  return (
    <StyledTextArea
      {...props}
      ref={ref}
    />
  )
})

import { RefObject } from 'react'

import { AppLanguage } from '~/services/language'
import Button from '~/components/button'
import TextArea from '~/components/textarea'
import { InputContainer } from '../style'

interface ChatInputProps {
  language : AppLanguage,
  textAreaRef : RefObject<HTMLTextAreaElement>,
  onSend : () => void
}

export default function ChatInput ({
  language,
  textAreaRef,
  onSend
} : ChatInputProps) {
  return (
    <InputContainer>
      <TextArea
        placeholder={language === 'zh' ? '\u6709\u95ee\u9898\uff0c\u5c3d\u7ba1\u95ee' : 'ask any questions'}
        ref={textAreaRef}
      />
      <Button onClick={onSend}>
        <svg width='24' height='24' viewBox='0 0 14 16'><path fill='currentColor' d='m6.2 0.9q0.2-0.2 0.4-0.2 0.2-0.1 0.4-0.1 0.2 0 0.4 0.1 0.2 0 0.4 0.2l5.2 5.1c0.2 0.3 0.3 0.6 0.3 0.9 0 0.2-0.2 0.5-0.4 0.7-0.2 0.3-0.5 0.4-0.8 0.4-0.3 0-0.5-0.1-0.8-0.3l-3.2-3.2v9.8c0 0.3-0.1 0.6-0.3 0.8-0.2 0.2-0.5 0.3-0.8 0.3-0.3 0-0.6-0.1-0.8-0.3-0.2-0.2-0.3-0.5-0.3-0.8v-9.8l-3.2 3.2c-0.2 0.2-0.5 0.3-0.8 0.3-0.4 0-0.7-0.1-0.9-0.3-0.2-0.2-0.3-0.5-0.3-0.8 0-0.3 0.1-0.6 0.3-0.9z' /></svg>
      </Button>
    </InputContainer>
  )
}

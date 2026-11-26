import { FormEvent, useState } from 'react'
import { Clarification, RoutingOption } from '~/entities/messages'
import { AppLanguage } from '~/services/language'

interface Props {
  clarification : Clarification,
  language : AppLanguage,
  busy : boolean,
  onSubmit : (answer : string, option ?: RoutingOption) => Promise<void>,
  onCancel : () => void
}

export default function ClarificationPanel ({ clarification, language, busy, onSubmit, onCancel } : Props) {
  const [answer, setAnswer] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const disabled = busy || submitting
  const zh = language === 'zh'
  const options = clarification.options || []
  const submit = async (option ?: RoutingOption) => {
    if (disabled || (!option && !answer.trim())) return
    setSubmitting(true)
    try { await onSubmit(answer, option) } finally { setSubmitting(false) }
  }
  const handleSubmit = (event : FormEvent) => {
    event.preventDefault()
    void submit()
  }
  return (
    <section aria-labelledby={`clarification-${clarification.clarification_id}`} className='hitlPanel clarificationPanel'>
      <h2 className='hitlPanelTitle' id={`clarification-${clarification.clarification_id}`}>
        {clarification.kind === 'routing'
          ? (zh ? '确认会话或补充需求' : 'Choose a conversation or add details')
          : (zh ? '补充信息后继续' : 'Add details to continue')}
      </h2>
      <p>{clarification.prompt}</p>
      <form onSubmit={handleSubmit}>
        <label>
          {zh ? '补充说明' : 'Additional details'}
          <textarea disabled={disabled} maxLength={4000} onChange={(event) => setAnswer(event.target.value)} value={answer} />
        </label>
        <div className='clarificationActions'>
          {options.map((option) => (
            <button className='hitlConfirmButton' disabled={disabled} key={`${option.kind}-${option.conversation_id || ''}`}
              onClick={() => { void submit(option) }} type='button'>
              {option.kind === 'new' ? (zh ? '新建会话' : 'New conversation') : option.title}
            </button>
          ))}
          {!options.length && <button className='hitlConfirmButton' disabled={disabled || !answer.trim()} type='submit'>
            {zh ? '补充并继续' : 'Continue with details'}
          </button>}
          <button className='clarificationCancel' disabled={disabled} onClick={onCancel} type='button'>
            {clarification.kind === 'routing' ? (zh ? '返回编辑' : 'Back to editing') : (zh ? '取消此任务' : 'Cancel this task')}
          </button>
        </div>
      </form>
    </section>
  )
}

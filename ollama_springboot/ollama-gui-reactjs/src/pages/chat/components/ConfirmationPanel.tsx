interface ConfirmationPanelProps {
  isAwaitingConfirmation : boolean,
  pendingSubQuestions : string[],
  confirmLoading : boolean,
  pendingSessionId ?: string,
  onConfirm : () => void
}

export default function ConfirmationPanel ({
  isAwaitingConfirmation,
  pendingSubQuestions,
  confirmLoading,
  pendingSessionId,
  onConfirm
} : ConfirmationPanelProps) {
  if (!isAwaitingConfirmation) {
    return null
  }

  return (
    <div className='hitlPanel'>
      <div className='hitlPanelTitle'>Generated sub-questions, please confirm to continue</div>
      <ul className='hitlQuestionList'>
        {pendingSubQuestions.map((question, idx) => (
          <li key={`${idx}-${question}`}>{question}</li>
        ))}
      </ul>
      <button
        className='hitlConfirmButton'
        disabled={confirmLoading || !pendingSessionId}
        onClick={onConfirm}
        type='button'
      >
        {confirmLoading ? 'Confirming...' : 'Confirm and Continue'}
      </button>
    </div>
  )
}

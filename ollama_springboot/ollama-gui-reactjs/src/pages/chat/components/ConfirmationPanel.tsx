import { TaskStatus } from '../useChatLogic'

interface ConfirmationPanelProps {
  taskStatus : TaskStatus,
  subQuestions : string[],
  isConfirming : boolean,
  taskId ?: string,
  onConfirm : () => Promise<void>
}

export default function ConfirmationPanel ({
  taskStatus,
  subQuestions,
  isConfirming,
  taskId,
  onConfirm
} : ConfirmationPanelProps) {
  if (taskStatus !== 'PENDING_CONFIRM') {
    return null
  }

  const hasSubQuestions = subQuestions.length > 0
  const canConfirm = !!taskId && !isConfirming

  return (
    <div className='hitlPanel'>
      <div className='hitlPanelTitle'>Generated sub-questions, please confirm to continue</div>
      {hasSubQuestions && (
        <ul className='hitlQuestionList'>
          {subQuestions.map((question, idx) => (
            <li key={`${idx}-${question}`}>{question}</li>
          ))}
        </ul>
      )}
      {!hasSubQuestions && (
        <p className='systemMessage' style={{ marginBottom: 12 }}>
          Recovering pending sub-questions from backend...
        </p>
      )}
      {!taskId && (
        <p className='systemMessage' style={{ marginBottom: 12 }}>
          Task id missing, please refresh task state.
        </p>
      )}
      <button
        className='hitlConfirmButton'
        disabled={!canConfirm}
        onClick={() => { void onConfirm() }}
        type='button'
      >
        {isConfirming ? 'Confirming...' : 'Confirm and Continue'}
      </button>
    </div>
  )
}

import { useAppLanguage } from '~/services/language'
import About from '~/components/about'
import { ColumnContainer, RowContainer } from '~/components/containers'
import Menu from '~/components/menu'
import LOADING from '~/assets/images/loading.png'
import { Loading } from './style'
import ChatErrorBoundary from './components/ChatErrorBoundary'
import ChatInput from './components/ChatInput'
import ConfirmationPanel from './components/ConfirmationPanel'
import MessageList from './components/MessageList'
import useChatLogic from './useChatLogic'
import './index.css'

export default function Chat () {
  const language = useAppLanguage()
  const {
    rowContainerRef,
    textAreaRef,
    messages,
    hasTalk,
    taskId,
    taskStatus,
    subQuestions,
    finalResult,
    loading,
    error,
    handleDeleteChat,
    requestHandler,
    handleConfirmClicked
  } = useChatLogic()

  const isConfirming = taskStatus === 'CONFIRMING'
  const showConfirming = isConfirming
  const showGenerating = taskStatus === 'GENERATING'
  const hasFinalInMessages = finalResult.trim().length > 0
    && messages.some(
      (message) => message.role === 'assistant' && message.content.trim() === finalResult.trim()
    )
  const showFinalResult = taskStatus === 'DONE' && finalResult.trim().length > 0 && !hasFinalInMessages

  return (
    <ChatErrorBoundary>
      <RowContainer ref={rowContainerRef}>
        <Menu onDeleteChat={handleDeleteChat} scrollRef={rowContainerRef} />
        <ColumnContainer style={{ padding: 8, paddingRight: 0 }}>
          { (loading || showConfirming) && <Loading src={LOADING} /> }
          { hasTalk ? <MessageList messages={messages} /> : <About /> }

          {showConfirming && (
            <div className='hitlPanel'>
              <div className='hitlPanelTitle'>Confirming...</div>
              <p>Submitting confirmation and syncing task state.</p>
            </div>
          )}

          {showGenerating && (
            <div className='hitlPanel'>
              <div className='hitlPanelTitle'>Generating final result...</div>
              <p>{finalResult.trim().length > 0 ? finalResult : 'The backend is still processing your request.'}</p>
            </div>
          )}

          {showFinalResult && (
            <div className='hitlPanel'>
              <div className='hitlPanelTitle'>Final result</div>
              <p>{finalResult}</p>
            </div>
          )}

          {taskStatus === 'ERROR' && error && (
            <p className='systemMessage'>{error}</p>
          )}

          <ConfirmationPanel
            isConfirming={isConfirming}
            onConfirm={handleConfirmClicked}
            subQuestions={subQuestions}
            taskId={taskId}
            taskStatus={taskStatus}
          />
          <ChatInput
            language={language}
            onSend={requestHandler}
            textAreaRef={textAreaRef}
          />
        </ColumnContainer>
      </RowContainer>
    </ChatErrorBoundary>
  )
}

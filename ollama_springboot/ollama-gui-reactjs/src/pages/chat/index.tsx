import { useAppLanguage } from '~/services/language'
import About from '~/components/about'
import { ColumnContainer, RowContainer } from '~/components/containers'
import Menu from '~/components/menu'
import LOADING from '~/assets/images/loading.png'
import { Loading } from './style'
import ChatErrorBoundary from './components/ChatErrorBoundary'
import ChatInput from './components/ChatInput'
import ConfirmationPanel from './components/ConfirmationPanel'
import ClarificationPanel from './components/ClarificationPanel'
import { routeProgress } from '~/services/routing'
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
    loading,
    error,
    pendingImages,
    apiBaseUrl,
    routingMode,
    setRoutingMode,
    routingClarification,
    pendingInput,
    nextStep,
    handleRoutingChoice,
    handleRoutingDismiss,
    handleInputSubmit,
    handleCancelWaiting,
    handleDeleteChat,
    requestHandler,
    handleConfirmClicked,
    handleImagesSelected,
    handleImageRemoved
  } = useChatLogic()

  const isConfirming = taskStatus === 'CONFIRMING'
  const showConfirming = isConfirming

  return (
    <ChatErrorBoundary>
      <RowContainer ref={rowContainerRef}>
        <Menu onDeleteChat={handleDeleteChat} scrollRef={rowContainerRef} />
        <ColumnContainer style={{ padding: 8, paddingRight: 0 }}>
          { (loading || showConfirming) && <Loading src={LOADING} /> }
          { hasTalk ? <MessageList apiBaseUrl={apiBaseUrl} messages={messages} /> : <About /> }
          {nextStep && <p aria-live='polite' className='systemMessage'>{routeProgress(nextStep, language)}</p>}

          {routingClarification && <ClarificationPanel
            key={routingClarification.clarification_id}
            busy={loading}
            clarification={routingClarification}
            language={language}
            onCancel={handleRoutingDismiss}
            onSubmit={(answer, option) => handleRoutingChoice(option, answer)}
          />}
          {pendingInput && <ClarificationPanel
            key={pendingInput.clarification_id}
            busy={loading}
            clarification={pendingInput}
            language={language}
            onCancel={() => { void handleCancelWaiting() }}
            onSubmit={handleInputSubmit}
          />}

          {showConfirming && (
            <div className='hitlPanel'>
              <div className='hitlPanelTitle'>Confirming...</div>
              <p>Submitting confirmation and syncing task state.</p>
            </div>
          )}

          {error && (
            <p className='systemMessage' role='alert'>{error}</p>
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
            images={pendingImages}
            onImageRemoved={handleImageRemoved}
            onImagesSelected={handleImagesSelected}
            onSend={requestHandler}
            textAreaRef={textAreaRef}
            routingMode={routingMode}
            onRoutingModeChange={setRoutingMode}
            disabled={loading || Boolean(routingClarification) || taskStatus === 'PENDING_INPUT' || taskStatus === 'PENDING_CONFIRM'}
          />
        </ColumnContainer>
      </RowContainer>
    </ChatErrorBoundary>
  )
}

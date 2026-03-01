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
    loading,
    confirmLoading,
    isAwaitingConfirmation,
    pendingSubQuestions,
    pendingSessionId,
    handleDeleteChat,
    requestHandler,
    handleConfirmClicked
  } = useChatLogic()

  return (
    <ChatErrorBoundary>
      <RowContainer ref={rowContainerRef}>
        <Menu loading={loading || confirmLoading} onDeleteChat={handleDeleteChat} scrollRef={rowContainerRef} />
        <ColumnContainer style={{ padding: 8, paddingRight: 0 }}>
          { (loading || confirmLoading) && <Loading src={LOADING} /> }
          { hasTalk ? <MessageList messages={messages} /> : <About /> }
          <ConfirmationPanel
            confirmLoading={confirmLoading}
            isAwaitingConfirmation={isAwaitingConfirmation}
            onConfirm={handleConfirmClicked}
            pendingSessionId={pendingSessionId}
            pendingSubQuestions={pendingSubQuestions}
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

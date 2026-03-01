import React, { ErrorInfo, ReactNode } from 'react'

interface ChatErrorBoundaryProps {
  children : ReactNode
}

interface ChatErrorBoundaryState {
  hasError : boolean
}

export default class ChatErrorBoundary extends React.Component<ChatErrorBoundaryProps, ChatErrorBoundaryState> {
  state : ChatErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError () : ChatErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch (error : Error, errorInfo : ErrorInfo) {
    console.error('chat-render-failed', {
      error,
      errorInfo
    })
  }

  render () {
    if (this.state.hasError) {
      return (
        <div className='systemMessage' style={{ margin: '16px' }}>
          Chat failed to render. Please refresh the page.
        </div>
      )
    }
    return this.props.children
  }
}

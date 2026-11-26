import { RoutingMode } from '~/entities/messages'

/** A candidate is deliberately weaker than an explicitly selected destination. */
export function routingFields (mode : RoutingMode, conversationId ?: string) {
  if (mode === 'auto') {
    return { routing_mode: 'auto' as const, current_conversation_id: conversationId }
  }
  if (mode === 'new' || !conversationId) return { routing_mode: 'new' as const }
  return {
    routing_mode: 'explicit' as const,
    conversation_id: conversationId,
    session_id: conversationId
  }
}

export function routeProgress (nextStep : string, language : 'en' | 'zh') {
  const labels : Record<string, [string, string]> = {
    retrieve: ['Finding supporting information…', '正在查找资料…'],
    refine: ['Checking the remaining questions…', '正在补充检索…'],
    answer: ['Preparing the answer…', '正在整理回答…'],
    clarify: ['More information is needed.', '需要补充信息。']
  }
  return labels[nextStep]?.[language === 'zh' ? 1 : 0] || ''
}

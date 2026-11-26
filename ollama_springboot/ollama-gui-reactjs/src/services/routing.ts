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

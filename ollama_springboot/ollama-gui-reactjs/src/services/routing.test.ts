import { routingFields } from './routing'

test('automatic routing only sends a weak candidate', () => {
  const payload = routingFields('auto', 'conversation-a')
  expect(payload).toEqual({ routing_mode: 'auto', current_conversation_id: 'conversation-a' })
  expect(payload).not.toHaveProperty('conversation_id')
  expect(payload).not.toHaveProperty('session_id')
})

test('explicit current and new conversation use distinct signals', () => {
  expect(routingFields('explicit', 'a')).toEqual({ routing_mode: 'explicit', conversation_id: 'a', session_id: 'a' })
  expect(routingFields('explicit')).toEqual({ routing_mode: 'new' })
  expect(routingFields('new', 'a')).toEqual({ routing_mode: 'new' })
})

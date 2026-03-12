export interface Message {
  role : 'assistant' | 'user' | 'system',
  content : string,
  time : number,
  conversationId ?: string,
  sessionId ?: string
}

export type Messages = Message[]

export type Chats = Message[][]

interface HitlEnvelope {
  status ?: string,
  awaiting_human_confirmation ?: boolean,
  pending_sub_questions ?: string[],
  final_answer ?: string
}

interface ModelDefaultResponse extends HitlEnvelope {
  created_at : string,
  done : boolean,
  message : Message,
  model : string,
  conversation_id ?: string,
  session_id ?: string
}

interface ModelFinalResponse extends HitlEnvelope {
  created_at : string,
  done : boolean,
  done_reason : string,
  eval_count : number,
  eval_duration : number,
  load_duration : number,
  message : Message,
  model : string,
  prompt_eval_duration : number,
  total_duration : number,
  conversation_id ?: string,
  session_id ?: string
}

export type ModelResponse = ModelDefaultResponse | ModelFinalResponse

export interface ConfirmPayload {
  session_id : string,
  action : 'confirm' | 'edit',
  sub_questions : string[],
  comment : string
}

export type SessionStateRole = 'assistant' | 'user' | 'system'

export interface SessionStateMessage {
  role : SessionStateRole,
  content : string
}

export interface SessionStateResponse {
  session_id : string,
  conversation_id ?: string,
  facts : Record<string, string>,
  messages : SessionStateMessage[],
  updated_at : number | string,
  awaiting_human_confirmation ?: boolean,
  pending_sub_questions ?: string[],
  awaitingHumanConfirmation ?: boolean,
  pendingSubQuestions ?: string[]
}

export type BackendTaskStatus =
  | 'PENDING_CONFIRM'
  | 'CONFIRMED'
  | 'GENERATING'
  | 'DONE'
  | 'FAILED'
  | 'EXPIRED'
  | string

export interface RecommendTaskStateResponse {
  task_id ?: string,
  taskId ?: string,
  conversation_id ?: string,
  conversationId ?: string,
  status : BackendTaskStatus,
  pending_sub_questions ?: unknown,
  pendingSubQuestions ?: unknown,
  sub_questions ?: unknown,
  subQuestions ?: unknown,
  final_result ?: string,
  finalResult ?: string,
  error_message ?: string,
  errorMessage ?: string,
  progress ?: number,
  last_heartbeat_at ?: string,
  lastHeartbeatAt ?: string,
  expire_at ?: string,
  expireAt ?: string,
  created_at ?: string,
  createdAt ?: string,
  updated_at ?: string,
  updatedAt ?: string
}

export interface ConversationCreateRequest {
  title ?: string,
  model_name ?: string
}

export interface ConversationCreateResponse {
  conversation_id : string,
  created_at ?: string
}

export interface RecommendTaskCreateRequest {
  query : string,
  conversation_id ?: string,
  session_id ?: string,
  timeout ?: number,
  max_iterations ?: number
}

export interface RecommendTaskConfirmRequest {
  sub_questions ?: string[],
  comment ?: string
}

export type ChatStreamEventType =
  | 'meta'
  | 'node'
  | 'token'
  | 'state'
  | 'awaiting_confirmation'
  | 'final'
  | 'error'

interface ChatStreamBaseEvent {
  type : ChatStreamEventType,
  task_id ?: string,
  conversation_id ?: string,
  session_id ?: string
}

export interface ChatStreamMetaEvent extends ChatStreamBaseEvent {
  type : 'meta',
  status : string
}

export interface ChatStreamNodeEvent extends ChatStreamBaseEvent {
  type : 'node',
  node : string,
  status : string,
  payload_keys : string[]
}

export interface ChatStreamTokenEvent extends ChatStreamBaseEvent {
  type : 'token',
  delta : string,
  node : string
}

export interface ChatStreamStateEvent extends ChatStreamBaseEvent {
  type : 'state',
  payload : Record<string, unknown>
}

export interface ChatStreamAwaitingConfirmationEvent extends ChatStreamBaseEvent {
  type : 'awaiting_confirmation',
  sub_questions : string[]
}

export interface ChatStreamFinalEvent extends ChatStreamBaseEvent {
  type : 'final',
  status : string,
  final_answer : string,
  retrieved_doc_ids : string[]
}

export interface ChatStreamErrorEvent extends ChatStreamBaseEvent {
  type : 'error',
  message : string
}

export type ChatStreamEvent =
  | ChatStreamMetaEvent
  | ChatStreamNodeEvent
  | ChatStreamTokenEvent
  | ChatStreamStateEvent
  | ChatStreamAwaitingConfirmationEvent
  | ChatStreamFinalEvent
  | ChatStreamErrorEvent

// Backward-compat alias for existing imports.
export type RecommendStreamEventType = ChatStreamEventType
export type RecommendStreamEvent = ChatStreamEvent

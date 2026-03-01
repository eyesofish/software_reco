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

export interface RecommendTaskStateResponse {
  task_id ?: string,
  taskId ?: string,
  status : 'PENDING_CONFIRM' | 'CONFIRMED' | 'GENERATING' | 'DONE' | 'FAILED' | string,
  sub_questions ?: string[],
  subQuestions ?: string[],
  final_result ?: string,
  finalResult ?: string,
  created_at ?: string,
  createdAt ?: string,
  updated_at ?: string,
  updatedAt ?: string
}

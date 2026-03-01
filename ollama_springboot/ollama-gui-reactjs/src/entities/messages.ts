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

export interface SessionStateMessage {
  role : 'assistant' | 'user' | 'system',
  content : string
}

export interface SessionStateResponse {
  session_id : string,
  facts : Record<string, string>,
  messages : SessionStateMessage[],
  updated_at : number
}

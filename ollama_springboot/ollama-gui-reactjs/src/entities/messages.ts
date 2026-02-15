export interface Message {
  role : 'assistant' | 'user',
  content : string,
  time : number,
  conversationId ?: string,
  sessionId ?: string
}

export type Messages = Message[]

export type Chats = Message[][]

interface ModelDefaultResponse {
  created_at : string,
  done : boolean,
  message : Message,
  model : string,
  conversation_id ?: string,
  session_id ?: string
}

interface ModelFinalResponse {
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

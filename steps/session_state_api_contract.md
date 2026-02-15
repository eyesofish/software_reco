# Session State API Contract

## Spring Boot

### GET `/api/session-state/{conversation_id}`
- Purpose: Read persisted session facts for a conversation.
- Path params:
  - `conversation_id` (string, required)
- Response:
```json
{
  "conversation_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "session_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "facts": {
    "user_name": "Qiu Yuchen"
  },
  "updated_at": "2026-02-15T22:01:33Z"
}
```

### PUT `/api/session-state/{conversation_id}`
- Purpose: Upsert persisted session facts for a conversation.
- Path params:
  - `conversation_id` (string, required)
- Request body:
```json
{
  "user_name": "Qiu Yuchen",
  "facts": {
    "user_name": "Qiu Yuchen"
  }
}
```
- Response: same schema as GET.

## FastAPI

### GET `/api/v1/session-state/{session_id}`
- Purpose: Read in-memory session facts used by agent runtime.
- Path params:
  - `session_id` (string, required)
- Response:
```json
{
  "session_id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "facts": {
    "user_name": "Qiu Yuchen"
  },
  "updated_at": 1768500000.123
}
```

### PUT `/api/v1/session-state/{session_id}`
- Purpose: Upsert in-memory session facts used by agent runtime.
- Path params:
  - `session_id` (string, required)
- Request body:
```json
{
  "facts": {
    "user_name": "Qiu Yuchen"
  }
}
```
- Response: same schema as GET.

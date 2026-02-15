#### API Contract

GET /api/conversations
Request:
- Query Params:
  - `page` (int, optional, default=1): 页码，从 1 开始
  - `page_size` (int, optional, default=20, max=100): 每页条数
  - `q` (string, optional): 按会话标题/首条消息模糊搜索
  - `sort` (string, optional, default=`updated_at:desc`): 排序规则，支持 `updated_at:desc|asc`
  - `include_archived` (bool, optional, default=false): 是否包含已归档会话

Example Request:
```http
GET /api/conversations?page=1&page_size=20&q=redis&sort=updated_at:desc HTTP/1.1
Host: localhost:8080
Authorization: Bearer <token>
```

Response:
- `items` (array):
  - `id` (string, uuid)
  - `title` (string)
  - `preview` (string, 会话预览文本，建议取首条 user 消息或最新消息截断)
  - `message_count` (int)
  - `last_message_role` (string, `user|assistant`)
  - `last_message_at` (string, ISO8601)
  - `updated_at` (string, ISO8601)
  - `created_at` (string, ISO8601)
  - `archived` (bool)
- `pagination` (object):
  - `page` (int)
  - `page_size` (int)
  - `total` (int)
  - `total_pages` (int)
  - `has_next` (bool)
  - `has_prev` (bool)

Example Response:
```json
{
  "items": [
    {
      "id": "7ef3fd64-18e4-4bd2-9d5f-2d6887bf2cbf",
      "title": "Redis vs Memcached for Spring Boot",
      "preview": "How should I choose cache for high QPS API?",
      "message_count": 14,
      "last_message_role": "assistant",
      "last_message_at": "2026-02-15T11:52:01Z",
      "updated_at": "2026-02-15T11:52:01Z",
      "created_at": "2026-02-15T11:40:20Z",
      "archived": false
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 57,
    "total_pages": 3,
    "has_next": true,
    "has_prev": false
  }
}
```

POST /api/conversations
Request:
- Body:
  - `title` (string, optional): 不传则后端基于首条 user 消息自动生成
  - `first_message` (object, required):
    - `role` (string, required, must=`user`)
    - `content` (string, required, 1..4000)
  - `model` (string, optional): 会话默认模型，如 `llama3`

Example Request:
```http
POST /api/conversations HTTP/1.1
Host: localhost:8080
Content-Type: application/json
Authorization: Bearer <token>

{
  "title": "RAG architecture discussion",
  "first_message": {
    "role": "user",
    "content": "Help me design RAG for Spring Boot service."
  },
  "model": "llama3"
}
```

Response:
- `id` (string, uuid)
- `title` (string)
- `created_at` (string, ISO8601)
- `updated_at` (string, ISO8601)
- `message_count` (int)
- `messages` (array, 初始可返回 1 条或按需要返回空数组)

Example Response:
```json
{
  "id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "title": "RAG architecture discussion",
  "created_at": "2026-02-15T12:01:33Z",
  "updated_at": "2026-02-15T12:01:33Z",
  "message_count": 1,
  "messages": [
    {
      "id": "4f0df9f1-7f2e-4c5b-9d86-d3a67f0f0c1d",
      "role": "user",
      "content": "Help me design RAG for Spring Boot service.",
      "created_at": "2026-02-15T12:01:33Z"
    }
  ]
}
```

PATCH /api/conversations/{id}
Request:
- Path Params:
  - `id` (string, uuid, required)
- Body (至少一个字段):
  - `title` (string, optional, 1..200)
  - `archived` (bool, optional)
  - `pinned` (bool, optional)

Example Request:
```http
PATCH /api/conversations/51f4dfcc-0189-4e95-b52f-cbe2394c6f9e HTTP/1.1
Host: localhost:8080
Content-Type: application/json
Authorization: Bearer <token>

{
  "title": "RAG architecture for Java backend",
  "pinned": true
}
```

Response:
- `id` (string)
- `title` (string)
- `archived` (bool)
- `pinned` (bool)
- `updated_at` (string, ISO8601)

Example Response:
```json
{
  "id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "title": "RAG architecture for Java backend",
  "archived": false,
  "pinned": true,
  "updated_at": "2026-02-15T12:06:48Z"
}
```

DELETE /api/conversations/{id}
Request:
- Path Params:
  - `id` (string, uuid, required)
- Query Params:
  - `hard` (bool, optional, default=false): `false` 为软删除（归档/删除标记），`true` 为物理删除

Example Request:
```http
DELETE /api/conversations/51f4dfcc-0189-4e95-b52f-cbe2394c6f9e?hard=false HTTP/1.1
Host: localhost:8080
Authorization: Bearer <token>
```

Response:
- `id` (string)
- `deleted` (bool)
- `hard` (bool)
- `deleted_at` (string, ISO8601)

Example Response:
```json
{
  "id": "51f4dfcc-0189-4e95-b52f-cbe2394c6f9e",
  "deleted": true,
  "hard": false,
  "deleted_at": "2026-02-15T12:09:05Z"
}
```

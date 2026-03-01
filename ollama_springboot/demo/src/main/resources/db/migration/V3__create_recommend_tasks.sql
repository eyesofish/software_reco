CREATE TABLE recommend_tasks (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    fastapi_session_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    sub_questions TEXT,
    final_result TEXT,
    error_message TEXT,
    progress INTEGER,
    last_heartbeat_at TIMESTAMP WITH TIME ZONE,
    expire_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT fk_recommend_tasks_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX idx_recommend_tasks_conversation_created
  ON recommend_tasks(conversation_id, created_at DESC);

CREATE INDEX idx_recommend_tasks_status_heartbeat
  ON recommend_tasks(status, last_heartbeat_at);

CREATE INDEX idx_recommend_tasks_fastapi_session
  ON recommend_tasks(fastapi_session_id);
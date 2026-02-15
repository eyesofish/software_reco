CREATE TABLE conversations (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    preview VARCHAR(500),
    model_name VARCHAR(128),
    message_count INTEGER NOT NULL DEFAULT 0,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    last_message_role VARCHAR(16),
    last_message_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX idx_conversation_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_conversation_deleted_archived ON conversations(deleted, archived);

CREATE TABLE conversation_messages (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT fk_conversation_messages_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX idx_message_conversation_created_at
  ON conversation_messages(conversation_id, created_at ASC);

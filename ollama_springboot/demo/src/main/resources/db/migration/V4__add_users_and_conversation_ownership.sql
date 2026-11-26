CREATE TABLE app_users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);
ALTER TABLE conversations ADD COLUMN owner_user_id VARCHAR(36);
ALTER TABLE conversations ADD CONSTRAINT fk_conversations_owner
    FOREIGN KEY (owner_user_id) REFERENCES app_users(id);
CREATE INDEX idx_conversations_owner_updated ON conversations(owner_user_id, updated_at DESC);

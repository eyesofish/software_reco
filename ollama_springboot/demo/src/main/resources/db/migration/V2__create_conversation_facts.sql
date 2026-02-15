CREATE TABLE conversation_facts (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    fact_key VARCHAR(64) NOT NULL,
    fact_value VARCHAR(500) NOT NULL,
    source_message_id VARCHAR(36),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT fk_conversation_facts_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    CONSTRAINT uk_conversation_facts_key UNIQUE (conversation_id, fact_key)
);

CREATE INDEX idx_conversation_facts_updated_at
  ON conversation_facts(conversation_id, updated_at DESC);

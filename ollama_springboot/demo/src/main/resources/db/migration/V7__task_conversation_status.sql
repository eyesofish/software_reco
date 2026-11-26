CREATE INDEX idx_recommend_tasks_conversation_status
    ON recommend_tasks (conversation_id, status);

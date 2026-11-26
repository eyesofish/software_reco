CREATE TABLE chat_submissions (
    owner_user_id VARCHAR(36) NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    request_id VARCHAR(36) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    task_id VARCHAR(36) REFERENCES recommend_tasks(id) ON DELETE SET NULL,
    PRIMARY KEY (owner_user_id, request_id)
);

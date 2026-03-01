export const CONFIRM_ONLY_PATTERN = /^(?:\u786e\u8ba4|\u7ee7\u7eed|\u7ee7\u7eed\u5427|\u597d\u7684|\u597d|ok|okay|yes|y|go on|continue)[.!?\u3002\uff01\uff1f]*$/i
export const RECOVERY_POLL_INTERVAL_MS = 2000
export const RECOVERY_POLL_TIMEOUT_MS = 90_000
export const CONFIRM_STATUS_POLL_INTERVAL_MS = 2000
export const CONFIRM_STATUS_POLL_TIMEOUT_MS = 10 * 60_000
export const CONFIRM_RETRY_INTERVAL_TICKS = 3
export const WAITING_ACK_PATTERN = /waiting\s+for\s+confirmation|\u5f85\u786e\u8ba4|\u7b49\u5f85\u786e\u8ba4|\u786e\u8ba4\u540e\u7ee7\u7eed/i
export const PENDING_LINE_PATTERN = /^\s*\d+[.)\u3001]\s*(.+?)\s*$/
export const TASK_ID_STORAGE_PREFIX = 'reco_task_id_chat_'
export const CONFIRM_PENDING_STORAGE_PREFIX = 'reco_confirm_pending_chat_'

export const REQUEST_FAILED_MESSAGE = 'Something went wrong :-('
export const CONFIRM_GUIDE_MESSAGE = 'Please click "Confirm and Continue" instead of sending "confirm/continue/ok" as a new question.'

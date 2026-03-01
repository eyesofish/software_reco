export const CONFIRM_ONLY_PATTERN = /^(?:\u786e\u8ba4|\u7ee7\u7eed|\u7ee7\u7eed\u5427|\u597d\u7684|\u597d|ok|okay|yes|y|go on|continue)[.!?\u3002\uff01\uff1f]*$/i

export const TASK_POLL_INTERVAL_MS = 1500

export const CHAT_CONVERSATION_IDS_STORAGE_KEY = 'reco_chat_conversation_ids'
export const TASK_ID_STORAGE_PREFIX = 'reco_task_id_conversation_'

export const REQUEST_FAILED_MESSAGE = 'Something went wrong :-('
export const CONFIRM_FAILED_MESSAGE = 'Confirmation failed. Please retry.'
export const CONFIRM_GUIDE_MESSAGE = 'Please click "Confirm and Continue" instead of sending "confirm/continue/ok" as a new question.'
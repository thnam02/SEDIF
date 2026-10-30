export const TASK_TYPES = ["saved_payee", "new_typed", "new_pasted", "qr"] as const
export type TaskType = (typeof TASK_TYPES)[number]

export const SESSION_EVENT_TYPES = [
  "field_focus",
  "field_blur",
  "key_tick",
  "paste",
  "qr_scan",
  "saved_payee_pick",
  "field_edit",
  "screen_enter",
  "app_background",
  "app_foreground",
  "confirm_tap",
] as const

export type SessionEventType = (typeof SESSION_EVENT_TYPES)[number]

/** Chỉ loại sự kiện và mốc monotonic. Không có ký tự, số tài khoản, số tiền hay toạ độ. */
export type SessionEvent = {
  type: SessionEventType
  t: number
  field?: string
  screen?: string
}

export type BehaviourFeatures = {
  median_gap_ms: number
  p90_gap_ms: number
  burst_count: number
  pause_to_action_ratio: number
  review_dwell_ms: number
  edit_count: number
  backtrack_count: number
  screen_revisit_count: number
  background_count: number
  background_total_ms: number
  session_duration_ms: number
}

export type FeatureConfig = {
  /** Khoảng cách nhỏ hơn mức này thì các thao tác liên tiếp là một burst. */
  burst_gap_ms?: number
}

export const DEFAULT_BURST_GAP_MS = 1500

export type CallStateName = "active" | "ended_in_session" | "none" | "unknown"
export type CallObservability = "supported_observed" | "supported_not_observed" | "unavailable"
export type CallTransport = "cellular" | "voip" | "unknown"

export type CallBlock = {
  state: CallStateName
  observability: CallObservability
  transport: CallTransport
}

import type { components } from "../api/openapi"

/** Ca guided: median cùng loại × 1,8 pause, × 1,6 p90, × 4 thời gian nền, +2 lần rời app. */
export const GUIDED_FEATURES: components["schemas"]["BehaviourFeatures"] = {
  median_gap_ms: 805.2112291861524,
  p90_gap_ms: 3530.681805960459,
  burst_count: 2,
  pause_to_action_ratio: 0.32398482638721315,
  review_dwell_ms: 1410,
  edit_count: 1,
  backtrack_count: 0,
  screen_revisit_count: 1,
  background_count: 3,
  background_total_ms: 7209,
  session_duration_ms: 41592,
}

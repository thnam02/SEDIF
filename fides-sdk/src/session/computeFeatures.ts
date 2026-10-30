import {
  DEFAULT_BURST_GAP_MS,
  type BehaviourFeatures,
  type FeatureConfig,
  type SessionEvent,
} from "./types"

const ZERO: BehaviourFeatures = {
  median_gap_ms: 0,
  p90_gap_ms: 0,
  burst_count: 0,
  pause_to_action_ratio: 0,
  review_dwell_ms: 0,
  edit_count: 0,
  backtrack_count: 0,
  screen_revisit_count: 0,
  background_count: 0,
  background_total_ms: 0,
  session_duration_ms: 0,
}

function ordered(events: SessionEvent[]): SessionEvent[] {
  return events
    .map((event, index) => ({ event, index }))
    .sort((left, right) => left.event.t - right.event.t || left.index - right.index)
    .map((item) => item.event)
}

function gapsOf(events: SessionEvent[]): number[] {
  const gaps: number[] = []
  for (let index = 1; index < events.length; index += 1) {
    gaps.push(Math.max(0, events[index].t - events[index - 1].t))
  }
  return gaps
}

function median(sorted: number[]): number {
  if (sorted.length === 0) return 0
  const mid = Math.floor(sorted.length / 2)
  if (sorted.length % 2 === 1) return sorted[mid]
  return (sorted[mid - 1] + sorted[mid]) / 2
}

/** Nội suy tuyến tính, hạng (n − 1) × 0.9. */
function percentile90(sorted: number[]): number {
  if (sorted.length === 0) return 0
  if (sorted.length === 1) return sorted[0]
  const rank = (sorted.length - 1) * 0.9
  const low = Math.floor(rank)
  const high = Math.ceil(rank)
  if (low === high) return sorted[low]
  const weight = rank - low
  return sorted[low] * (1 - weight) + sorted[high] * weight
}

function burstCount(gaps: number[], limit: number): number {
  let count = 0
  let run = 1
  for (const gap of gaps) {
    if (gap < limit) {
      run += 1
      continue
    }
    if (run >= 2) count += 1
    run = 1
  }
  if (run >= 2) count += 1
  return count
}

function backgroundSpans(events: SessionEvent[]): Array<[number, number]> {
  const spans: Array<[number, number]> = []
  let open: number | null = null
  const end = events.length === 0 ? 0 : events[events.length - 1].t
  for (const event of events) {
    if (event.type === "app_background") {
      if (open == null) open = event.t
    } else if (event.type === "app_foreground" && open != null) {
      spans.push([open, Math.max(open, event.t)])
      open = null
    }
  }
  if (open != null && end > open) spans.push([open, end])
  return spans
}

function overlap(spans: Array<[number, number]>, start: number, end: number): number {
  let total = 0
  for (const [left, right] of spans) {
    const from = Math.max(left, start)
    const to = Math.min(right, end)
    if (to > from) total += to - from
  }
  return total
}

function navigation(events: SessionEvent[]): { backtrack: number; revisit: number } {
  const seen = new Set<string>()
  const stack: string[] = []
  let backtrack = 0
  let revisit = 0
  for (const event of events) {
    if (event.type !== "screen_enter" || !event.screen) continue
    const name = event.screen
    if (seen.has(name)) revisit += 1
    seen.add(name)
    if (stack.length === 0 || stack[stack.length - 1] === name) {
      if (stack.length === 0) stack.push(name)
      continue
    }
    const index = stack.lastIndexOf(name)
    if (index >= 0) {
      backtrack += 1
      stack.splice(index + 1)
      continue
    }
    stack.push(name)
  }
  return { backtrack, revisit }
}

function reviewDwell(events: SessionEvent[], spans: Array<[number, number]>): number {
  let confirmAt: number | null = null
  for (const event of events) {
    if (event.type === "confirm_tap") confirmAt = event.t
  }
  if (confirmAt == null) return 0
  let enteredAt: number | null = null
  for (const event of events) {
    if (event.type === "screen_enter" && event.t <= confirmAt) enteredAt = event.t
  }
  if (enteredAt == null) return 0
  const span = confirmAt - enteredAt
  return Math.max(0, Math.round(span - overlap(spans, enteredAt, confirmAt)))
}

/**
 * Khoảng cách là hiệu mốc liên tiếp.
 * Burst là một dãy ít nhất hai thao tác mà mọi khoảng ở giữa nhỏ hơn burst_gap_ms.
 * pause_to_action_ratio là tổng khoảng ≥ ngưỡng chia cho tổng khoảng nhỏ hơn ngưỡng.
 * Không có khoảng thao tác thì tỉ lệ là 0.
 * review_dwell_ms là thời gian từ lần vào màn cuối trước confirm_tap tới confirm_tap,
 * trừ thời gian app ở nền trong cửa sổ đó.
 */
export function computeFeatures(events: SessionEvent[], config: FeatureConfig = {}): BehaviourFeatures {
  const limit = config.burst_gap_ms ?? DEFAULT_BURST_GAP_MS
  const rows = ordered(events)
  if (rows.length === 0) return { ...ZERO }
  const gaps = gapsOf(rows)
  const sorted = [...gaps].sort((left, right) => left - right)
  const pause = gaps.filter((gap) => gap >= limit).reduce((sum, gap) => sum + gap, 0)
  const action = gaps.filter((gap) => gap < limit).reduce((sum, gap) => sum + gap, 0)
  const spans = backgroundSpans(rows)
  const nav = navigation(rows)
  return {
    median_gap_ms: median(sorted),
    p90_gap_ms: percentile90(sorted),
    burst_count: burstCount(gaps, limit),
    pause_to_action_ratio: action > 0 ? pause / action : 0,
    review_dwell_ms: reviewDwell(rows, spans),
    edit_count: rows.filter((event) => event.type === "field_edit").length,
    backtrack_count: nav.backtrack,
    screen_revisit_count: nav.revisit,
    background_count: rows.filter((event) => event.type === "app_background").length,
    background_total_ms: Math.round(spans.reduce((sum, [left, right]) => sum + (right - left), 0)),
    session_duration_ms: Math.round(rows[rows.length - 1].t - rows[0].t),
  }
}

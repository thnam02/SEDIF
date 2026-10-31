/** Tối đa một kỳ. Tuần bắt đầu thứ Hai. */
export const STATS_BUCKET_CAP = 13

export type StatsPeriodId = "1m" | "3m" | "6m" | "1y"

const PERIODS: Record<StatsPeriodId, { months: number; grain: "week" | "month" }> = {
  "1m": { months: 1, grain: "week" },
  "3m": { months: 3, grain: "week" },
  "6m": { months: 6, grain: "month" },
  "1y": { months: 12, grain: "month" },
}

export type StatsTx = { ts: string; direction: string; amount: number; status?: string }

function settled(status: string | undefined): boolean {
  return (status ?? "done") === "done"
}

export type StatsBucket = {
  startMs: number
  label: string
  incoming: number
  outgoing: number
  count: number
}

export type StatsReport = {
  startMs: number
  endMs: number
  buckets: StatsBucket[]
  incoming: number
  outgoing: number
  count: number
}

/** ≥ 1 tỷ hoặc ≥ 1 triệu thì rút gọn. Còn lại giữ dấu chấm ngăn cách. */
export function compactVnd(amount: number): string {
  const sign = amount < 0 ? "−" : ""
  const abs = Math.abs(amount)
  if (abs >= 1_000_000_000) return `${sign}${scaled(abs / 1_000_000_000, 2)} tỷ`
  if (abs >= 1_000_000) return `${sign}${scaled(abs / 1_000_000, 1)} tr`
  return `${sign}${Math.trunc(abs).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".")}`
}

/** Nhãn trục dùng cùng đơn vị với đỉnh, để vạch giữa không rơi về số thô. */
export function axisVnd(value: number, peak: number): string {
  if (value === 0) return "0"
  const abs = Math.abs(value)
  const sign = value < 0 ? "−" : ""
  if (peak >= 1_000_000_000) return `${sign}${scaled(abs / 1_000_000_000, 2)} tỷ`
  if (peak >= 1_000_000) return `${sign}${scaled(abs / 1_000_000, 1)} tr`
  return compactVnd(value)
}

function scaled(value: number, digits: number): string {
  return value.toFixed(digits).replace(/\.?0+$/, "").replace(".", ",")
}

function addMonths(date: Date, delta: number): Date {
  const next = new Date(date.getTime())
  const day = next.getDate()
  next.setDate(1)
  next.setMonth(next.getMonth() + delta)
  const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate()
  next.setDate(Math.min(day, last))
  return next
}

function mondayOf(date: Date): Date {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const weekday = day.getDay()
  day.setDate(day.getDate() - (weekday === 0 ? 6 : weekday - 1))
  return day
}

function monthStart(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

function addDays(date: Date, count: number): Date {
  const next = new Date(date.getTime())
  next.setDate(next.getDate() + count)
  return next
}

function weekLabel(monday: Date): string {
  return `${monday.getDate()}/${monday.getMonth() + 1}`
}

function monthLabel(month: Date): string {
  return `T${month.getMonth() + 1}`
}

/** Cửa sổ [start, now] và các mốc rỗng. Quá 13 mốc thì giữ các mốc mới nhất. */
export function statsRange(period: StatsPeriodId, now: Date): { startMs: number; endMs: number; marks: { startMs: number; label: string }[] } {
  const spec = PERIODS[period]
  const origin = addMonths(now, -spec.months)
  const marks: { startMs: number; label: string }[] = []
  if (spec.grain === "week") {
    const end = mondayOf(now)
    for (let cursor = mondayOf(origin); cursor.getTime() <= end.getTime(); cursor = addDays(cursor, 7)) {
      marks.push({ startMs: cursor.getTime(), label: weekLabel(cursor) })
    }
  } else {
    const end = monthStart(now)
    for (let cursor = monthStart(origin); cursor.getTime() <= end.getTime(); cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1)) {
      marks.push({ startMs: cursor.getTime(), label: monthLabel(cursor) })
    }
  }
  const kept = marks.slice(-STATS_BUCKET_CAP)
  const startMs = kept[0]?.startMs ?? origin.getTime()
  return { startMs, endMs: now.getTime(), marks: kept }
}

export function buildStats(entries: StatsTx[], period: StatsPeriodId, now = new Date()): StatsReport {
  const range = statsRange(period, now)
  const buckets: StatsBucket[] = range.marks.map((mark) => ({ ...mark, incoming: 0, outgoing: 0, count: 0 }))
  let incoming = 0
  let outgoing = 0
  let count = 0
  for (const entry of entries) {
    const at = Date.parse(entry.ts)
    if (!Number.isFinite(at) || at < range.startMs || at > range.endMs) continue
    if (!settled(entry.status)) continue
    let index = 0
    for (let cursor = 0; cursor < buckets.length; cursor += 1) {
      if ((buckets[cursor]?.startMs ?? 0) <= at) index = cursor
      else break
    }
    const bucket = buckets[index]
    if (!bucket) continue
    const amount = Math.abs(entry.amount)
    if (entry.direction === "in") {
      bucket.incoming += amount
      incoming += amount
    } else {
      bucket.outgoing += amount
      outgoing += amount
    }
    bucket.count += 1
    count += 1
  }
  return { startMs: range.startMs, endMs: range.endMs, buckets, incoming, outgoing, count }
}

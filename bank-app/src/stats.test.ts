import assert from "node:assert/strict"
import test from "node:test"

import { axisVnd, buildStats, compactVnd, STATS_BUCKET_CAP, type StatsPeriodId, type StatsTx } from "./stats.ts"

function localIso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0")
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? "+" : "-"
  const abs = Math.abs(offset)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
}

const now = new Date(2026, 9, 31, 18, 0, 0)
const periods: StatsPeriodId[] = ["1m", "3m", "6m", "1y"]

function inside(entries: StatsTx[], period: StatsPeriodId): number {
  const report = buildStats(entries, period, now)
  return entries.filter((entry) => {
    const at = Date.parse(entry.ts)
    return at >= report.startMs && at <= report.endMs
  }).length
}

test("compact amounts and an empty period stay within the bucket cap", () => {
  assert.equal(compactVnd(1_770_000_000), "1,77 tỷ")
  assert.equal(compactVnd(126_900_000), "126,9 tr")
  assert.equal(compactVnd(1_000_000), "1 tr")
  assert.equal(compactVnd(12_500), "12.500")
  assert.equal(compactVnd(-2_500_000), "−2,5 tr")
  assert.equal(axisVnd(1_348_056, 1_348_056), "1,3 tr")
  assert.equal(axisVnd(674_028, 1_348_056), "0,7 tr")
  assert.equal(axisVnd(0, 1_348_056), "0")
  for (const period of periods) {
    const report = buildStats([], period, now)
    assert.ok(report.buckets.length > 0)
    assert.ok(report.buckets.length <= STATS_BUCKET_CAP)
    assert.equal(report.buckets.reduce((sum, bucket) => sum + bucket.count, 0), 0)
    assert.equal(report.count, 0)
  }
})

test("one transaction lands in a single bucket", () => {
  const entries = [{ ts: localIso(now), direction: "in" as const, amount: 500_000 }]
  for (const period of periods) {
    const report = buildStats(entries, period, now)
    assert.equal(report.count, 1)
    assert.equal(report.incoming, 500_000)
    assert.equal(report.outgoing, 0)
    assert.equal(report.buckets.reduce((sum, bucket) => sum + bucket.count, 0), 1)
    assert.equal(report.buckets.filter((bucket) => bucket.count > 0).length, 1)
  }
})

test("308 transactions across more than two years stay within 13 buckets", () => {
  const start = new Date(2024, 0, 15, 9, 0, 0)
  const span = now.getTime() - start.getTime()
  const entries: StatsTx[] = Array.from({ length: 308 }, (_, index) => ({
    ts: localIso(new Date(start.getTime() + (span * index) / 307)),
    direction: index % 3 === 0 ? "in" : "out",
    amount: 1_000_000 + index,
  }))
  assert.ok(span > 2 * 365 * 24 * 60 * 60 * 1000)
  for (const period of periods) {
    const report = buildStats(entries, period, now)
    assert.ok(report.buckets.length <= STATS_BUCKET_CAP)
    assert.equal(report.buckets.reduce((sum, bucket) => sum + bucket.count, 0), report.count)
    assert.equal(report.count, inside(entries, period))
    assert.ok(report.count < 308)
    assert.ok(report.count > 0)
  }
})

test("a transaction on a bucket boundary belongs to the later bucket", () => {
  const report = buildStats([], "1m", now)
  const boundary = report.buckets[1]
  assert.ok(boundary)
  const entries: StatsTx[] = [
    { ts: localIso(new Date(report.startMs)), direction: "out", amount: 10 },
    { ts: localIso(new Date(report.startMs - 1000)), direction: "out", amount: 99 },
    { ts: localIso(new Date(boundary.startMs)), direction: "in", amount: 20 },
    { ts: localIso(now), direction: "in", amount: 30 },
    { ts: localIso(new Date(now.getTime() + 1000)), direction: "in", amount: 40 },
  ]
  const placed = buildStats(entries, "1m", now)
  assert.equal(placed.count, 3)
  assert.equal(placed.buckets.reduce((sum, bucket) => sum + bucket.count, 0), 3)
  assert.equal(placed.buckets[0]?.count, 1)
  assert.equal(placed.buckets[0]?.outgoing, 10)
  assert.equal(placed.buckets[1]?.count, 1)
  assert.equal(placed.buckets[1]?.incoming, 20)
  const last = placed.buckets[placed.buckets.length - 1]
  assert.equal(last?.incoming, 30)
  assert.equal(placed.buckets.filter((bucket) => bucket.startMs === boundary.startMs).length, 1)
})

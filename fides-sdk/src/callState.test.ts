import assert from "node:assert/strict"
import test from "node:test"

import { deviceBlockFromCall, mapObservedCalls, reconcileCalls, type SeenCall } from "./callState.ts"

const idle = { in_call: false, startedAtMs: null as number | null }

test("cuộc đổ chuông đến là inbound và đang trong cuộc", () => {
  const mapped = mapObservedCalls([{ hasEnded: false, hasConnected: false, isOutgoing: false }], idle, 1_000)
  assert.equal(mapped.in_call, true)
  assert.equal(mapped.call_direction, "inbound")
  assert.equal(mapped.phase, "started")
  assert.equal(mapped.call_started_at_ms, 1_000)
})

test("cuộc đã nối máy đi là outbound, mốc giờ không bị ghi đè", () => {
  const mapped = mapObservedCalls([{ hasEnded: false, hasConnected: true, isOutgoing: true }], { in_call: true, startedAtMs: 500 }, 2_000)
  assert.equal(mapped.call_direction, "outbound")
  assert.equal(mapped.phase, null)
  assert.equal(mapped.call_started_at_ms, 500)
})

test("cuộc kết thúc trong phiên đặt phase ended và call_ended_in_session", () => {
  const ended = { hasEnded: true, hasConnected: true, isOutgoing: false }
  const mapped = mapObservedCalls([ended], { in_call: true, startedAtMs: 500 }, 4_000, ended)
  assert.equal(mapped.in_call, false)
  assert.equal(mapped.phase, "ended")
  assert.equal(mapped.call_direction, "inbound")
  assert.equal(mapped.call_started_at_ms, 500)
  const block = deviceBlockFromCall({
    platform: "ios",
    inCall: false,
    startedAtMs: mapped.call_started_at_ms,
    liveDirection: null,
    rememberedDirection: mapped.call_direction,
    sessionOpen: true,
    endedInSession: mapped.phase === "ended",
    now: 4_000,
    frozenSeconds: 3,
  })
  assert.deepEqual(block, {
    in_call: false,
    call_seconds_since_open: 3,
    call_ended_in_session: true,
    call_direction: "inbound",
  })
})

test("còn một cuộc chưa kết thúc thì vẫn in_call", () => {
  const mapped = mapObservedCalls(
    [
      { hasEnded: true, hasConnected: true, isOutgoing: true },
      { hasEnded: false, hasConnected: false, isOutgoing: false },
    ],
    { in_call: true, startedAtMs: 100 },
    200,
  )
  assert.equal(mapped.in_call, true)
  assert.equal(mapped.call_direction, "inbound")
})

test("Android bỏ call_direction dù native có hướng", () => {
  const block = deviceBlockFromCall({
    platform: "android",
    inCall: true,
    startedAtMs: 0,
    liveDirection: "outbound",
    rememberedDirection: "outbound",
    sessionOpen: true,
    endedInSession: false,
    now: 5_000,
    frozenSeconds: null,
  })
  assert.equal(block.call_direction, null)
  assert.equal(block.call_seconds_since_open, 5)
  assert.equal(block.call_ended_in_session, false)
})

test("app ở nền khi cuộc gọi kết thúc, về foreground thì call_ended_in_session", () => {
  const seen: SeenCall[] = [
    { uuid: "call-1", hasEnded: false, reported: false, direction: "inbound", firstSeenAtMs: 1_000 },
  ]
  const woke = reconcileCalls({
    seen,
    current: [],
    sessionOpen: true,
    alreadyEnded: false,
    now: 8_000,
  })
  assert.equal(woke.phase, "ended")
  assert.equal(woke.in_call, false)
  assert.equal(woke.call_direction, "inbound")
  assert.equal(woke.call_started_at_ms, 1_000)
  assert.equal(woke.call_ended_in_session, true)
  const block = deviceBlockFromCall({
    platform: "ios",
    inCall: woke.in_call,
    startedAtMs: woke.call_started_at_ms,
    liveDirection: null,
    rememberedDirection: woke.call_direction,
    sessionOpen: true,
    endedInSession: woke.call_ended_in_session === true,
    now: 8_000,
    frozenSeconds: 7,
  })
  assert.equal(block.call_ended_in_session, true)
  assert.equal(block.call_direction, "inbound")
})

test("về foreground thấy hasEnded thì vẫn kết thúc trong phiên", () => {
  const woke = reconcileCalls({
    seen: [{ uuid: "call-1", hasEnded: false, reported: false, direction: "outbound", firstSeenAtMs: 1_000 }],
    current: [{ uuid: "call-1", hasEnded: true, direction: "outbound" }],
    sessionOpen: true,
    alreadyEnded: false,
    now: 3_000,
  })
  assert.equal(woke.phase, "ended")
  assert.equal(woke.call_direction, "outbound")
  assert.equal(woke.call_ended_in_session, true)
})

test("Android đọc lại telephony lúc về foreground, hướng cuộc gọi vẫn null", () => {
  const woke = reconcileCalls({
    seen: [{ uuid: "telephony", hasEnded: false, reported: false, direction: null, firstSeenAtMs: 2_000 }],
    current: [],
    sessionOpen: true,
    alreadyEnded: false,
    now: 6_000,
  })
  const block = deviceBlockFromCall({
    platform: "android",
    inCall: woke.in_call,
    startedAtMs: woke.call_started_at_ms,
    liveDirection: woke.call_direction,
    rememberedDirection: woke.call_direction,
    sessionOpen: true,
    endedInSession: woke.phase === "ended",
    now: 6_000,
    frozenSeconds: 4,
  })
  assert.equal(woke.phase, "ended")
  assert.equal(block.call_ended_in_session, true)
  assert.equal(block.call_direction, null)
})

test("chưa mở phiên thì call_ended_in_session là null", () => {
  const block = deviceBlockFromCall({
    platform: "ios",
    inCall: false,
    startedAtMs: null,
    liveDirection: null,
    rememberedDirection: null,
    sessionOpen: false,
    endedInSession: true,
    now: 0,
    frozenSeconds: null,
  })
  assert.equal(block.call_ended_in_session, null)
  assert.equal(block.call_seconds_since_open, null)
})

export type CallDirection = "inbound" | "outbound"

/** Một cuộc CallKit. Không có số điện thoại. */
export type ObservedCall = {
  hasEnded: boolean
  hasConnected: boolean
  isOutgoing: boolean
}

export type CallSnapshot = {
  in_call: boolean
  call_direction: CallDirection | null
  call_started_at_ms: number | null
  phase: "started" | "ended" | null
}

/**
 * in_call khi còn cuộc chưa kết thúc: đang đổ chuông hoặc đã nối máy.
 * call_direction lấy cuộc đang nối, nếu không thì cuộc đang đổ.
 * call_started_at_ms là lần đầu observer thấy cuộc đó.
 */
export function mapObservedCalls(
  calls: ObservedCall[],
  previous: { in_call: boolean; startedAtMs: number | null },
  now: number,
  changed?: ObservedCall,
): CallSnapshot {
  const active = calls.filter((call) => !call.hasEnded)
  const inCall = active.length > 0
  const current = active.find((call) => call.hasConnected) ?? active[0] ?? (changed?.hasEnded ? changed : undefined)
  let direction: CallDirection | null = null
  if (current != null) direction = current.isOutgoing ? "outbound" : "inbound"
  let started = previous.startedAtMs
  let phase: "started" | "ended" | null = null
  if (inCall && !previous.in_call) {
    phase = "started"
    if (started == null) started = now
  } else if (!inCall && previous.in_call) {
    phase = "ended"
  }
  if (inCall && started == null) started = now
  return {
    in_call: inCall,
    call_direction: direction,
    call_started_at_ms: started,
    phase,
  }
}

/** Cuộc đã thấy trong phiên. direction null trên Android. Không có số điện thoại. */
export type SeenCall = {
  uuid: string
  hasEnded: boolean
  reported: boolean
  direction: CallDirection | null
  firstSeenAtMs: number
}

export type ReconcileResult = {
  in_call: boolean
  phase: "ended" | null
  call_direction: CallDirection | null
  call_started_at_ms: number | null
  call_ended_in_session: boolean | null
  seen: SeenCall[]
}

/**
 * Đọc lại danh sách cuộc khi app về foreground.
 * UUID đã thấy mà biến mất hoặc hasEnded → phase ended, giữ hướng cuộc đó.
 */
export function reconcileCalls(input: {
  seen: SeenCall[]
  current: Array<{ uuid: string; hasEnded: boolean; direction: CallDirection | null }>
  sessionOpen: boolean
  alreadyEnded: boolean
  now: number
}): ReconcileResult {
  const currentById = new Map(input.current.map((call) => [call.uuid, call]))
  const next = input.seen.map((call) => ({ ...call }))
  for (const call of input.current) {
    if (call.hasEnded || next.some((seen) => seen.uuid === call.uuid)) continue
    next.push({
      uuid: call.uuid,
      hasEnded: false,
      reported: false,
      direction: call.direction,
      firstSeenAtMs: input.now,
    })
  }
  const lost: SeenCall[] = []
  for (const call of next) {
    if (call.reported) continue
    const live = currentById.get(call.uuid)
    const gone = live == null || live.hasEnded
    if (!gone) continue
    call.hasEnded = true
    call.reported = true
    lost.push(call)
  }
  const active = input.current.filter((call) => !call.hasEnded)
  const inCall = active.length > 0
  if (lost.length === 0) {
    const currentDirection = active[0]?.direction ?? null
    return {
      in_call: inCall,
      phase: null,
      call_direction: currentDirection,
      call_started_at_ms: inCall ? (next.find((call) => !call.hasEnded)?.firstSeenAtMs ?? null) : null,
      call_ended_in_session: input.sessionOpen ? input.alreadyEnded : null,
      seen: next,
    }
  }
  const picked = lost.reduce((best, call) => (call.firstSeenAtMs >= best.firstSeenAtMs ? call : best))
  return {
    in_call: inCall,
    phase: "ended",
    call_direction: picked.direction,
    call_started_at_ms: picked.firstSeenAtMs,
    call_ended_in_session: input.sessionOpen ? true : null,
    seen: next,
  }
}

/** Android luôn null. iOS giữ hướng của cuộc vừa thấy, kể cả khi cuộc đã kết thúc trong phiên. */
export function directionForDevice(
  platform: string,
  live: CallDirection | null,
  remembered: CallDirection | null,
): CallDirection | null {
  if (platform !== "ios") return null
  return live ?? remembered
}

export function deviceBlockFromCall(input: {
  platform: string
  inCall: boolean | null
  startedAtMs: number | null
  liveDirection: CallDirection | null
  rememberedDirection: CallDirection | null
  sessionOpen: boolean
  endedInSession: boolean
  now: number
  frozenSeconds: number | null
}): {
  in_call: boolean | null
  call_seconds_since_open: number | null
  call_ended_in_session: boolean | null
  call_direction: CallDirection | null
} {
  const since = input.startedAtMs
  const seconds = input.frozenSeconds ?? (since === null ? null : Math.max(0, Math.floor((input.now - since) / 1000)))
  return {
    in_call: input.inCall,
    call_seconds_since_open: seconds,
    call_ended_in_session: input.sessionOpen ? input.endedInSession : null,
    call_direction: directionForDevice(input.platform, input.liveDirection, input.rememberedDirection),
  }
}

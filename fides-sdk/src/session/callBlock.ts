import type { CallBlock, CallObservability, CallTransport } from "./types"

export type CallSample = {
  in_call: boolean | null
  voip_suspected?: boolean
  observability?: CallObservability
  transport?: CallTransport
}

/** true khi còn cuộc. null khi nền tảng không trả lời. VoIP không có cuộc điện thoại vẫn là đang gọi. */
export function liveActive(sample: CallSample): boolean | null {
  if (sample.in_call === true) return true
  if (sample.voip_suspected) return true
  if (sample.in_call === false) return false
  return null
}

export function inferObservability(platform: string, sample: CallSample): CallObservability {
  if (sample.observability) return sample.observability
  if (sample.in_call === true || sample.in_call === false || sample.voip_suspected) return "supported_observed"
  if (platform === "ios") return "supported_not_observed"
  return "unavailable"
}

/** iOS không phân biệt di động và VoIP. Android: TelephonyCallback là cellular, AudioManager không kèm cuộc điện thoại là voip. */
export function inferTransport(platform: string, sample: CallSample): CallTransport {
  if (sample.transport) return sample.transport
  if (platform === "ios") return "unknown"
  if (sample.in_call === true) return "cellular"
  if (sample.voip_suspected) return "voip"
  return "unknown"
}

/**
 * none chỉ khi đã quan sát được và không có cuộc.
 * Không quan sát được thì unknown, kể cả khi activeNow là false.
 */
export function deriveCallBlock(input: {
  observability: CallObservability
  transport: CallTransport
  activeAtStart: boolean | null
  startedDuring: boolean
  endedDuring: boolean
  activeNow: boolean | null
}): CallBlock {
  if (input.observability !== "supported_observed") {
    return { state: "unknown", observability: input.observability, transport: input.transport }
  }
  if (input.activeNow === true) {
    return { state: "active", observability: "supported_observed", transport: input.transport }
  }
  const ended = input.endedDuring || (input.activeAtStart === true && input.activeNow === false)
  if (ended) {
    return { state: "ended_in_session", observability: "supported_observed", transport: input.transport }
  }
  if (input.activeNow === false) {
    return { state: "none", observability: "supported_observed", transport: input.transport }
  }
  return { state: "unknown", observability: "supported_not_observed", transport: input.transport }
}

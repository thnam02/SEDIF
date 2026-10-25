import { classifyAccountInput, type AccountInput, type InputEvent } from "./accountInput"

export type DeviceBlock = {
  in_call: boolean | null
  call_seconds_since_open: number | null
  call_ended_in_session: boolean | null
  call_direction: null
  acct_input: AccountInput | null
  app_switches: number | null
}

export type CallState = {
  in_call: boolean | null
  call_started_at_ms: number | null
  voip_suspected: boolean
}

type NativeCall = CallState & { phase?: "started" | "ended" }

type Simulation = { in_call: boolean; voip_suspected: boolean; startedAt: number | null }

let startedAtSession: number | null = null
let endedInSession = false
let switches = 0
let inputEvents: InputEvent[] = []
let simulation: Simulation | null = null
let nativeState: CallState = { in_call: null, call_started_at_ms: null, voip_suspected: false }
let frozenSeconds: number | null = null
let listening = false

function nativeModule(): { getCallState?: () => CallState; addListener?: (name: string, listener: (event: NativeCall) => void) => { remove: () => void } } | null {
  try {
    // expo-modules-core vắng khi chạy test Node; nạp lúc runtime.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const core = require("expo-modules-core") as { requireOptionalNativeModule?: (name: string) => unknown }
    return (core.requireOptionalNativeModule?.("Fides") as ReturnType<typeof nativeModule>) ?? null
  } catch {
    return null
  }
}

function applyNative(event: NativeCall) {
  nativeState = {
    in_call: event.in_call,
    call_started_at_ms: event.call_started_at_ms,
    voip_suspected: Boolean(event.voip_suspected),
  }
  if (event.call_started_at_ms != null) {
    nativeState = { ...nativeState, call_started_at_ms: event.call_started_at_ms }
  }
  if (startedAtSession !== null && event.phase === "ended") {
    endedInSession = true
    const started = event.call_started_at_ms
    if (started != null) {
      frozenSeconds = Math.max(0, Math.floor((Date.now() - started) / 1000))
    }
  }
}

export const FidesSession = {
  /** Gọi khi mở màn chuyển tiền. Không gửi gì lên engine. */
  start() {
    startedAtSession = Date.now()
    endedInSession = false
    frozenSeconds = null
    switches = 0
    inputEvents = []
    if (listening) {
      return
    }
    const native = nativeModule()
    native?.addListener?.("onCallStateChanged", applyNative)
    listening = true
  },

  noteAppSwitch() {
    if (startedAtSession !== null) {
      switches += 1
    }
  },

  noteInput(event: InputEvent) {
    inputEvents = [...inputEvents, event]
  },

  /** Chế độ giả lập cho demo khi máy không có cuộc gọi thật. */
  setSimulatedCall(active: boolean, voipSuspected = false) {
    if (!active) {
      if (simulation?.in_call && startedAtSession !== null) {
        endedInSession = true
        if (simulation.startedAt != null) {
          frozenSeconds = Math.max(0, Math.floor((Date.now() - simulation.startedAt) / 1000))
        }
      }
      simulation = { in_call: false, voip_suspected: voipSuspected, startedAt: simulation?.startedAt ?? null }
      return
    }
    frozenSeconds = null
    simulation = { in_call: true, voip_suspected: voipSuspected, startedAt: Date.now() }
  },

  callState(): CallState {
    if (simulation) {
      return {
        in_call: simulation.in_call,
        call_started_at_ms: simulation.startedAt,
        voip_suspected: simulation.voip_suspected,
      }
    }
    const native = nativeModule()
    if (native?.getCallState) {
      nativeState = native.getCallState()
    }
    return nativeState
  },

  collectSignals(): DeviceBlock {
    const state = this.callState()
    const since = state.call_started_at_ms
    const seconds = frozenSeconds ?? (since === null ? null : Math.max(0, Math.floor((Date.now() - since) / 1000)))
    return {
      in_call: state.in_call,
      call_seconds_since_open: seconds,
      call_ended_in_session: startedAtSession === null ? null : endedInSession,
      call_direction: null,
      acct_input: classifyAccountInput(inputEvents),
      app_switches: startedAtSession === null ? null : switches,
    }
  },
}

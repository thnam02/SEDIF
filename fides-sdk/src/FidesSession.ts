import { classifyAccountInput, type AccountInput, type InputEvent } from "./accountInput"
import { deviceBlockFromCall, type CallDirection } from "./callState"

export type DeviceBlock = {
  in_call: boolean | null
  call_seconds_since_open: number | null
  call_ended_in_session: boolean | null
  call_direction: CallDirection | null
  acct_input: AccountInput | null
  app_switches: number | null
}

export type CallState = {
  in_call: boolean | null
  call_started_at_ms: number | null
  voip_suspected: boolean
  call_direction?: CallDirection | null
}

type NativeCall = CallState & { phase?: "started" | "ended" }

type Simulation = { in_call: boolean; voip_suspected: boolean; startedAt: number | null }

let startedAtSession: number | null = null
let endedInSession = false
let switches = 0
let inputEvents: InputEvent[] = []
let simulation: Simulation | null = null
let nativeState: CallState = { in_call: null, call_started_at_ms: null, voip_suspected: false, call_direction: null }
let rememberedDirection: CallDirection | null = null
let frozenSeconds: number | null = null
let listening = false
let watchingApp = false

function onForeground() {
  const next = nativeModule()?.reconcile?.()
  if (next?.phase === "ended" || next?.phase === "started") {
    applyNative(next)
  }
}

function watchForeground() {
  if (watchingApp) return
  try {
    // react-native vắng khi chạy test Node.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const rn = require("react-native") as {
      AppState?: { addEventListener: (event: "change", listener: (state: string) => void) => { remove: () => void } }
    }
    rn.AppState?.addEventListener("change", (state) => {
      if (state === "active") onForeground()
    })
    watchingApp = true
  } catch {
    watchingApp = false
  }
}

function platformOs(): string {
  try {
    // react-native vắng khi chạy test Node.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const rn = require("react-native") as { Platform?: { OS?: string } }
    return rn.Platform?.OS ?? "android"
  } catch {
    return "android"
  }
}

function nativeModule(): {
  getCallState?: () => CallState
  beginSession?: () => void
  reconcile?: () => NativeCall
  addListener?: (name: string, listener: (event: NativeCall) => void) => { remove: () => void }
} | null {
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
  const live = event.call_direction ?? null
  if (live) rememberedDirection = live
  nativeState = {
    in_call: event.in_call,
    call_started_at_ms: event.call_started_at_ms,
    voip_suspected: Boolean(event.voip_suspected),
    call_direction: live,
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
    rememberedDirection = null
    frozenSeconds = null
    switches = 0
    inputEvents = []
    const native = nativeModule()
    native?.beginSession?.()
    if (listening) {
      return
    }
    native?.addListener?.("onCallStateChanged", applyNative)
    watchForeground()
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
        call_direction: null,
      }
    }
    const native = nativeModule()
    if (native?.getCallState) {
      const next = native.getCallState()
      const live = next.call_direction ?? null
      if (live) rememberedDirection = live
      nativeState = {
        in_call: next.in_call,
        call_started_at_ms: next.call_started_at_ms,
        voip_suspected: Boolean(next.voip_suspected),
        call_direction: live,
      }
    }
    return nativeState
  },

  collectSignals(): DeviceBlock {
    const state = this.callState()
    const simulated = simulation !== null
    const block = deviceBlockFromCall({
      platform: platformOs(),
      inCall: state.in_call,
      startedAtMs: state.call_started_at_ms,
      liveDirection: simulated ? null : (state.call_direction ?? null),
      rememberedDirection: simulated ? null : rememberedDirection,
      sessionOpen: startedAtSession !== null,
      endedInSession,
      now: Date.now(),
      frozenSeconds,
    })
    return {
      ...block,
      acct_input: classifyAccountInput(inputEvents),
      app_switches: startedAtSession === null ? null : switches,
    }
  },
}

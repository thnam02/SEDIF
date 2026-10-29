import { computeFeatures } from "./computeFeatures"
import {
  DEFAULT_BURST_GAP_MS,
  SESSION_EVENT_TYPES,
  TASK_TYPES,
  type BehaviourFeatures,
  type SessionEvent,
  type SessionEventType,
  type TaskType,
} from "./types"

const NAME = /^[a-z_]{1,32}$/

export type ExperimentConsent = {
  participantId: string
  condition: string
  sessionIndex: number
  consented: boolean
}

export type SessionSnapshot = {
  task_type: TaskType
  behaviour_features: BehaviourFeatures
  experiment?: { participant_id: string; condition: string; session_index: number }
  raw_events?: SessionEvent[]
}

type NoteInput = {
  type: SessionEventType
  t?: number
  field?: string
  screen?: string
  /** Chỉ ô người nhận mới đổi task_type. Không lưu vào sự kiện. */
  payee?: boolean
}

function monotonicNow(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now()
  }
  return Date.now()
}

function taskFrom(type: SessionEventType): TaskType | null {
  if (type === "saved_payee_pick") return "saved_payee"
  if (type === "paste") return "new_pasted"
  if (type === "qr_scan") return "qr"
  if (type === "key_tick") return "new_typed"
  return null
}

function sanitize(input: NoteInput, now: number): SessionEvent | null {
  if (!SESSION_EVENT_TYPES.includes(input.type)) return null
  const event: SessionEvent = { type: input.type, t: Number.isFinite(input.t) ? Number(input.t) : now }
  if ((input.type === "field_focus" || input.type === "field_blur") && input.field && NAME.test(input.field)) {
    event.field = input.field
  }
  if (input.type === "screen_enter" && input.screen && NAME.test(input.screen)) {
    event.screen = input.screen
  }
  return event
}

function clone(event: SessionEvent): SessionEvent {
  const copy: SessionEvent = { type: event.type, t: event.t }
  if (event.field) copy.field = event.field
  if (event.screen) copy.screen = event.screen
  return copy
}

type AppStateHandle = { remove: () => void }

export class SessionRecorder {
  private events: SessionEvent[] = []
  private running = false
  private taskType: TaskType = "new_typed"
  private burstGapMs = DEFAULT_BURST_GAP_MS
  private experiment: SessionSnapshot["experiment"] | null = null
  private stopWatch: (() => void) | null = null
  private backgrounded = false
  private readonly now: () => number

  constructor(now: () => number = monotonicNow) {
    this.now = now
  }

  isOpen(): boolean {
    return this.running
  }

  /** Ngưỡng burst. Mặc định 1500 ms, sau này server hiệu chỉnh theo task_type. */
  setBurstGap(ms: number) {
    if (Number.isFinite(ms) && ms > 0) this.burstGapMs = ms
  }

  start(taskType: TaskType) {
    if (!TASK_TYPES.includes(taskType)) return
    if (this.running) return
    this.running = true
    this.taskType = taskType
    this.events = []
    this.experiment = null
    this.backgrounded = false
    this.watchApp()
  }

  /** Giữ mốc thô chỉ khi có cờ và khách đồng ý. Bản sao chỉ để gửi endpoint thí nghiệm. */
  allowExperiment(input: ExperimentConsent) {
    if (!this.running || !input.consented || !input.participantId) {
      this.experiment = null
      return
    }
    this.experiment = {
      participant_id: input.participantId,
      condition: input.condition,
      session_index: input.sessionIndex,
    }
  }

  note(input: NoteInput) {
    if (!this.running) return
    const event = sanitize(input, this.now())
    if (!event) return
    this.events.push(event)
    const setsTask = input.payee || event.type === "saved_payee_pick" || event.type === "qr_scan"
    if (!setsTask) return
    const next = taskFrom(event.type)
    if (next) this.taskType = next
  }

  /** Ghi confirm_tap, tính đặc trưng, rồi xoá mốc thô. Chế độ thí nghiệm trả một bản sao để upload. */
  end(): SessionSnapshot {
    if (!this.running) {
      return { task_type: this.taskType, behaviour_features: computeFeatures([], { burst_gap_ms: this.burstGapMs }) }
    }
    this.note({ type: "confirm_tap" })
    const features = computeFeatures(this.events, { burst_gap_ms: this.burstGapMs })
    const raw = this.experiment ? this.events.map(clone) : undefined
    const snapshot: SessionSnapshot = {
      task_type: this.taskType,
      behaviour_features: features,
      experiment: this.experiment ?? undefined,
      raw_events: raw,
    }
    this.events = []
    this.running = false
    this.experiment = null
    this.backgrounded = false
    this.stopWatch?.()
    this.stopWatch = null
    return snapshot
  }

  private watchApp() {
    this.stopWatch?.()
    this.stopWatch = null
    try {
      // react-native vắng khi chạy test Node.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const rn = require("react-native") as {
        AppState?: { addEventListener: (event: "change", listener: (state: string) => void) => AppStateHandle }
      }
      const sub = rn.AppState?.addEventListener("change", (state) => {
        if (!this.running) return
        if (state === "background") {
          if (this.backgrounded) return
          this.backgrounded = true
          this.note({ type: "app_background" })
          return
        }
        if (state === "active" && this.backgrounded) {
          this.backgrounded = false
          this.note({ type: "app_foreground" })
        }
      })
      this.stopWatch = () => sub?.remove()
    } catch {
      this.stopWatch = null
    }
  }
}

export const sessionRecorder = new SessionRecorder()

import type { components } from "../generated/openapi"

type Decide = components["schemas"]["DecideResponse"]
type Respond = components["schemas"]["RespondResponse"]

export type FidesDecision = Omit<Pick<Decide, "decision_id" | "level" | "actions" | "reasons" | "hold_until" | "countdown_until">, "level"> &
  Partial<Pick<Decide, "p_fraud" | "signals_present" | "missing_signals" | "profile" | "model_version">> &
  Partial<Pick<Respond, "funds_status" | "gate">> & {
    level: Respond["level"]
    payee_name?: string
    amount_text?: string
    channel?: string
    chain_rows?: string[]
  }

export type FidesScreenId =
  | "info"
  | "banner"
  | "simo_listed"
  | "pause_and_verify"
  | "show_chain"
  | "guardian_confirm_requested"
  | "short_wait"
  | "hangup_required"
  | "countdown"
  | "held"
  | "cancelled"

export function fidesScreen(decision: FidesDecision): FidesScreenId {
  const actions = decision.actions ?? []
  const has = (name: string) => actions.includes(name)
  const simo = (decision.reasons ?? []).some((reason) => reason.code === "recipient_simo_flag" || reason.code === "simo_listed")
  if (decision.funds_status === "cancelled") return "cancelled"
  if (decision.gate === "short_wait") return "short_wait"
  if (decision.gate === "guardian_confirm_requested") return "guardian_confirm_requested"
  if (has("show_chain")) return "show_chain"
  if (simo) return "simo_listed"
  if (decision.level === "banner" || has("g1_banner")) return "banner"
  if (decision.funds_status === "held" || has("hold")) return "held"
  return "info"
}

export function remainingSeconds(until: string | null | undefined, now: number): number | null {
  if (!until) return null
  const end = Date.parse(until)
  if (Number.isNaN(end)) return null
  return Math.max(0, Math.ceil((end - now) / 1000))
}

export function formatRemaining(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${String(rest).padStart(2, "0")}`
}

const DEMO_CLOCK = new Date(2026, 8, 21)

function presentStamp(iso: string, now = new Date()): string {
  if (Number.isNaN(Date.parse(iso))) return iso
  const from = Date.UTC(DEMO_CLOCK.getFullYear(), DEMO_CLOCK.getMonth(), DEMO_CLOCK.getDate())
  const to = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
  const shift = Math.round((to - from) / 86_400_000)
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso))
  const pick = (type: string) => parts.find((item) => item.type === type)?.value ?? ""
  const day = new Date(Number(pick("year")), Number(pick("month")) - 1, Number(pick("day")))
  day.setDate(day.getDate() + shift)
  const month = String(day.getMonth() + 1).padStart(2, "0")
  const date = String(day.getDate()).padStart(2, "0")
  return `${day.getFullYear()}-${month}-${date}T${pick("hour").padStart(2, "0")}:${pick("minute").padStart(2, "0")}:${pick("second").padStart(2, "0")}+07:00`
}

export function formatHold(iso: string): string {
  const date = new Date(presentStamp(iso))
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  }).format(date)
}

import type { components } from "../generated/openapi"

type Decide = components["schemas"]["DecideResponse"]
type Respond = components["schemas"]["RespondResponse"]

export type FidesDecision = Omit<Pick<Decide, "decision_id" | "level" | "actions" | "reasons" | "hold_until" | "countdown_until">, "level"> &
  Partial<Pick<Decide, "p_fraud" | "group_scores" | "families_hit" | "missing_signals" | "profile" | "model_version">> &
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
  const simo = (decision.reasons ?? []).some((reason) => reason.code === "simo_listed")
  if (decision.funds_status === "cancelled") return "cancelled"
  const highCountdown = Boolean(decision.countdown_until) && (decision.level === "high" || has("hangup_required") || has("recovery_window"))
  if (highCountdown) return "countdown"
  if (has("hangup_required") || decision.level === "high") return "hangup_required"
  if (decision.gate === "short_wait") return "short_wait"
  if (decision.gate === "guardian_confirm_requested") return "guardian_confirm_requested"
  if (has("show_chain") && !has("pause_and_verify")) return "show_chain"
  if (has("pause_and_verify") || decision.level === "medium") return "pause_and_verify"
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

export function formatHold(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  }).format(date)
}

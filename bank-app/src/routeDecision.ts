export type ResultRoute = "success" | "success_reminder" | "three_button" | "security"

const RANK: Record<ResultRoute, number> = {
  success: 0,
  success_reminder: 1,
  three_button: 2,
  security: 3,
}

function routeForLevel(level: number): ResultRoute {
  if (level >= 3) return "security"
  if (level === 2) return "three_button"
  if (level === 1) return "success_reminder"
  return "success"
}

function routeForIntervention(intervention: string | null | undefined): ResultRoute {
  if (intervention === "security" || intervention === "security_workflow") return "security"
  if (intervention === "three_button") return "three_button"
  if (intervention === "reminder" || intervention === "banner") return "success_reminder"
  return "success"
}

/** Trước khi trừ tiền. Mức 1 là tờ nhắc. Mức 0 trừ và xong. Mức 2 và 3 giữ lệnh. */
export function confirmOutcome(level: number): "settle" | "reminder" | "hold" {
  if (level >= 2) return "hold"
  if (level === 1) return "reminder"
  return "settle"
}

/** Màn sau khi xác nhận. Mức số và kiểu can thiệp cùng trỏ một màn; mức cao hơn thắng. */
export function resultRoute(level: number, intervention?: string | null): ResultRoute {
  const fromLevel = routeForLevel(level)
  const fromIntervention = routeForIntervention(intervention)
  return RANK[fromIntervention] > RANK[fromLevel] ? fromIntervention : fromLevel
}

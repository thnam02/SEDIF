export const UNKNOWN_PAYEE = "Không tìm thấy tài khoản người nhận"
const PLACEHOLDER = "Người nhận"

export type PayeeLookup = "idle" | "pending" | "found" | "missing"

/** Số đã nhập xong: 10 chữ số, hoặc mã nội bộ có chữ. */
export function accountComplete(account: string): boolean {
  const token = account.replace(/\s/g, "")
  if (!token) return false
  const onlyDigits = token.replace(/\D/g, "")
  if (onlyDigits.length >= 10 && onlyDigits === token) return true
  return /[A-Za-z]/.test(token)
}

/** Tiếp tục chỉ khi sổ trả tên thật. Không đi tiếp với "Người nhận". */
export function payeeGate(account: string, status: PayeeLookup, holder: string): { canContinue: boolean; showMissing: boolean } {
  const token = account.replace(/\s/g, "")
  if (!token) return { canContinue: false, showMissing: false }
  if (status === "missing") return { canContinue: false, showMissing: accountComplete(token) }
  const name = holder.trim()
  if (status !== "found" || !name || name === PLACEHOLDER) return { canContinue: false, showMissing: false }
  return { canContinue: true, showMissing: false }
}

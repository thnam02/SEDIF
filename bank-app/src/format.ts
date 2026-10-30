const ICT = "Asia/Ho_Chi_Minh"
const day = new Intl.DateTimeFormat("en-GB", { timeZone: ICT, day: "2-digit", month: "2-digit" })
const dayFull = new Intl.DateTimeFormat("en-GB", { timeZone: ICT, day: "2-digit", month: "2-digit", year: "numeric" })
const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" })

export function digits(value: string): string {
  return value.replace(/\D/g, "")
}

export function grouped(amount: number): string {
  const whole = Math.trunc(Math.abs(amount)).toString()
  const text = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".")
  return amount < 0 ? `-${text}` : text
}

export function vnd(amount: number): string {
  return `${grouped(amount)} đ`
}

function part(iso: string, type: Intl.DateTimeFormatPartTypes, options: Intl.DateTimeFormatOptions): string {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: ICT, ...options }).formatToParts(new Date(iso))
  return parts.find((item) => item.type === type)?.value ?? ""
}

export function when(iso: string): { time: string; date: string } {
  const hour = part(iso, "hour", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
  const minute = part(iso, "minute", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
  const date = `${part(iso, "day", { day: "2-digit", month: "2-digit", year: "numeric" })}/${part(iso, "month", { day: "2-digit", month: "2-digit", year: "numeric" })}/${part(iso, "year", { day: "2-digit", month: "2-digit", year: "numeric" })}`
  return { time: `${hour}:${minute}`, date }
}

export function fullDate(iso: string): string {
  return dayFull.format(new Date(iso))
}

export function weekStart(iso: string): string {
  const date = new Date(`${dayKey.format(new Date(iso))}T12:00:00Z`)
  const weekday = date.getUTCDay()
  date.setUTCDate(date.getUTCDate() - (weekday === 0 ? 6 : weekday - 1))
  return day.format(date)
}

type Movement = { ts: string; direction: string; amount: number }

export function statement<T extends Movement>(entries: T[], closingBalance: number): (T & { balanceAfter: number })[] {
  const ordered = [...entries].sort((left, right) => (left.ts < right.ts ? -1 : left.ts > right.ts ? 1 : 0))
  let cursor = closingBalance
  const rows: (T & { balanceAfter: number })[] = []
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const item = ordered[index]
    if (!item) continue
    rows.push({ ...item, balanceAfter: cursor })
    cursor += item.direction === "in" ? -item.amount : item.amount
  }
  return rows
}

const MOVEMENT: Record<string, string> = {
  savings_liquidation: "Tất toán sổ tiết kiệm",
  quick_loan_disbursement: "Giải ngân khoản vay",
  loan_disbursement: "Giải ngân khoản vay",
  limit_increase: "Nâng hạn mức",
  limit_raise: "Nâng hạn mức",
  card_cash_advance: "Rút tiền mặt thẻ tín dụng",
  credit_card_cash_advance: "Rút tiền mặt thẻ tín dụng",
  salary_in: "Lương",
  business_in: "Thu kinh doanh",
  bill_payment: "Thanh toán hóa đơn",
  rent: "Tiền nhà",
  card_payment: "Thanh toán thẻ",
  cash_withdrawal: "Rút tiền",
  school_fee: "Học phí",
  large_oneoff: "Khoản chi lớn",
  crypto_purchase: "Mua tài sản số",
}

export function movementTitle(item: { tx_type: string; direction: string; counterparty_name?: string | null }): string {
  const name = item.counterparty_name?.trim()
  if (item.tx_type === "transfer" || item.tx_type === "own_transfer" || item.tx_type === "own_transfer_drain" || item.tx_type === "passthrough_in" || item.tx_type === "passthrough_out") {
    if (item.direction === "in") return name ? `Nhận tiền từ ${name}` : "Nhận tiền"
    return name ? `Chuyển tiền tới ${name}` : "Chuyển tiền"
  }
  return MOVEMENT[item.tx_type] ?? "Giao dịch"
}

/** Lấy số tài khoản trong chữ đã dán. Ngân hàng trên app demo luôn là BVC. */
export function parseTransferDraft(text: string): { account: string; bank: string } {
  const compact = text.toUpperCase().replace(/\s+/g, "")
  const digits = compact.match(/\d{6,}/)
  if (!digits || digits.index === undefined) return { account: "", bank: "BVC bank" }
  let prefix = ""
  for (let index = digits.index - 1; index >= 0 && digits.index - index <= 4; index -= 1) {
    const char = compact[index]
    if (!char || char < "A" || char > "Z") break
    prefix = char + prefix
  }
  return { account: `${prefix}${digits[0]}`, bank: "BVC bank" }
}

/** Cột 0 là thứ Hai. */
export function weekdayIndex(iso: string): number {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Ho_Chi_Minh", weekday: "short" }).format(new Date(iso))
  const order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  const index = order.indexOf(parts)
  return index < 0 ? 0 : index
}

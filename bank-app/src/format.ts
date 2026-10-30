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

/** Số hiển thị 10 chữ số, hậu tố khác nhau. Mã nội bộ không đưa ra màn hình. */
const DEMO_NUMBERS: Record<string, string> = {
  A0119434: "0901119434",
  ZCAP400000000001: "0902401001",
  ZGUIDE4000000001: "0902402001",
  ZMULEDEMO0000001: "0902003001",
  ZMED230000000001: "0902230230",
  ZMED220000000001: "0902220220",
  ZMED000000000001: "0902000001",
  ZLANB40000000001: "0902101002",
  ZLANB40000000002: "0902102002",
  ZLAN120000000001: "0902120120",
  ZANH000000000099: "0902000099",
  ZVAY000000000001: "0902004001",
}

function accountKey(id: string): string {
  return id.replace(/\s/g, "").toUpperCase()
}

export function displayDigits(id: string): string {
  const key = accountKey(id)
  if (DEMO_NUMBERS[key]) return DEMO_NUMBERS[key]
  const digits = key.replace(/\D/g, "")
  return digits.length >= 4 ? digits.padStart(10, "0").slice(-10) : digits
}

export function showAccount(id: string): string {
  const digits = displayDigits(id)
  if (digits.length < 10) return maskAccount(id)
  if (!DEMO_NUMBERS[accountKey(id)]) return maskAccount(id)
  return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`
}

export function maskAccount(id: string): string {
  const tail = displayDigits(id).slice(-4)
  return tail ? `•••• ${tail}` : ""
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

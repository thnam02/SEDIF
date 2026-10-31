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

/** Tiền tố ngân hàng thay cho số 0 đệm. HMAC vẫn băm cả mã nội bộ, không băm số này. */
const BANK_PREFIX = "1903"

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
  const mapped = DEMO_NUMBERS[key]
  const raw = mapped ?? key.replace(/\D/g, "")
  if (!raw) return ""
  if (mapped || raw.length >= 10) return raw.length > 10 ? raw.slice(-10) : raw
  return `${BANK_PREFIX}${raw.slice(-6).padStart(6, "0")}`
}

/** Nhóm 3 từ phải sang trái. Nhóm trái chỉ một chữ số thì gộp vào nhóm kế. */
export function groupAccount(raw: string): string {
  const digits = raw.replace(/\D/g, "")
  if (!digits) return ""
  const groups: string[] = []
  let rest = digits
  while (rest.length > 3) {
    groups.unshift(rest.slice(-3))
    rest = rest.slice(0, -3)
  }
  if (rest.length === 1 && groups.length > 0) {
    groups[0] = `${rest}${groups[0]}`
  } else if (rest) {
    groups.unshift(rest)
  }
  return groups.join(" ")
}

/** Số người nhận. Không che. */
export function showAccount(id: string): string {
  return groupAccount(displayDigits(id))
}

/** Chỉ che tài khoản nguồn của chính khách. */
export function maskAccount(id: string): string {
  const tail = displayDigits(id).slice(-4)
  return tail ? `•••• ${tail}` : ""
}

/** Một kiểu câu: chữ đầu viết hoa, kết thúc bằng dấu chấm. */
export function reasonLine(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, " ")
  if (!trimmed) return ""
  const chars = [...trimmed]
  const first = chars[0] ?? ""
  let line = first.toLocaleUpperCase("vi") + chars.slice(1).join("")
  if (!/[.!?]$/.test(line)) line += "."
  return line
}

function part(iso: string, type: Intl.DateTimeFormatPartTypes, options: Intl.DateTimeFormatOptions): string {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: ICT, ...options }).formatToParts(new Date(iso))
  return parts.find((item) => item.type === type)?.value ?? ""
}

function clockPart(iso: string, type: "hour" | "minute" | "second"): string {
  const value = part(iso, type, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
  return value.padStart(2, "0")
}

function localDay(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

function addDays(day: Date, count: number): Date {
  const next = new Date(day)
  next.setDate(next.getDate() + count)
  return next
}

function stampOn(day: Date, iso: string): string {
  const year = day.getFullYear()
  const month = String(day.getMonth() + 1).padStart(2, "0")
  const date = String(day.getDate()).padStart(2, "0")
  return `${year}-${month}-${date}T${clockPart(iso, "hour")}:${clockPart(iso, "minute")}:${clockPart(iso, "second")}+07:00`
}

function hashText(seed: string): number {
  let hash = 2166136261
  for (const char of seed) {
    hash ^= char.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

/** Khoảng cách 1–8 ngày, cùng một hạt cho cùng một mốc. */
function gapDays(seed: string): number {
  return 1 + (hashText(seed) % 8)
}

/**
 * Đưa ngày sổ cái về khoảng tính từ ngày máy.
 * Mới nhất trong 2 ngày gần đây. Các lệnh cách nhau 1–8 ngày. Giữ giờ phút gốc.
 */
export function presentHistory<T extends { ts: string; tx_id: string }>(entries: T[], now = new Date()): T[] {
  if (entries.length === 0) return entries
  const valid = entries.flatMap((entry, index) => {
    const ms = Date.parse(entry.ts)
    return Number.isFinite(ms) ? [{ index, ms }] : []
  })
  if (valid.length === 0) return entries
  valid.sort((left, right) => left.ms - right.ms || left.index - right.index)
  const newest = valid[valid.length - 1]
  if (!newest) return entries
  const newestTs = entries[newest.index]?.ts ?? ""
  let age = hashText(newestTs) % 2
  if (Date.parse(stampOn(addDays(localDay(now), -age), newestTs)) > now.getTime()) age = 1
  const anchor = addDays(localDay(now), -age)
  const assigned = new Map<number, string>()
  let offset = 0
  for (const item of [...valid].reverse()) {
    const source = entries[item.index]
    if (!source) continue
    assigned.set(item.index, stampOn(addDays(anchor, -offset), source.ts))
    offset += gapDays(source.tx_id)
  }
  let previous = Number.NEGATIVE_INFINITY
  for (const item of valid) {
    const source = entries[item.index]
    let ts = assigned.get(item.index)
    if (!source || !ts) continue
    let guard = 0
    while (Date.parse(ts) <= previous && guard < 45) {
      const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(ts)
      const day = match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : localDay(new Date(ts))
      ts = stampOn(addDays(day, 1), source.ts)
      guard += 1
    }
    assigned.set(item.index, ts)
    previous = Date.parse(ts)
  }
  return entries.map((entry, index) => {
    const ts = assigned.get(index)
    return ts ? { ...entry, ts } : entry
  })
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

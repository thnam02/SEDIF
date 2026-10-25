const clock = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit" })
const day = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit" })
const dayFull = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric" })
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

export function when(iso: string): { time: string; date: string } {
  const value = new Date(iso)
  return { time: clock.format(value), date: day.format(value) }
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

/** Cột 0 là thứ Hai. */
export function weekdayIndex(iso: string): number {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Ho_Chi_Minh", weekday: "short" }).format(new Date(iso))
  const order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  const index = order.indexOf(parts)
  return index < 0 ? 0 : index
}

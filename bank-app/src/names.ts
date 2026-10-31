import { personas } from "./personas"

const MARKS = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
const ASCII = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyydAAAAAAAAAAAAAAAAAEEEEEEEEEEEIIIIIOOOOOOOOOOOOOOOOOUUUUUUUUUUUYYYYYD"

export function nameAscii(value: string): string {
  let out = ""
  for (const ch of value) {
    const index = MARKS.indexOf(ch)
    out += index >= 0 ? ASCII[index] : ch
  }
  return out.toUpperCase()
}

const byAscii = new Map(personas.map((person) => [nameAscii(person.full_name), person.full_name]))

export function transferNote(fullName: string): string {
  return `${nameAscii(fullName)} chuyen tien`
}

export function displayHolder(holder: string | null | undefined): string {
  if (!holder) return ""
  return byAscii.get(holder.trim().toUpperCase()) ?? holder
}

/** Cùng danh sách tên ở bank-core DEMO_PAYEES. Chỉ để hiện khi sổ chưa trả tên. */
const DEMO_PAYEES: Record<string, string> = {
  A0119434: "Công ty TNHH Thành Công",
  ZCAP400000000001: "Đặng Quốc Bảo",
  ZGUIDE4000000001: "Ngô Thanh Hà",
  ZMULEDEMO0000001: "Trịnh Văn Khôi",
  ZMED230000000001: "Lý Minh Châu",
  ZMED220000000001: "Lý Hồng Nhung",
  ZMED000000000001: "Lý Hồng Nhung",
  ZLANB40000000001: "Phạm Thu Ngân",
  ZLANB40000000002: "Mai Phương Thảo",
  ZLAN120000000001: "Phạm Thu Ngân",
  ZANH000000000099: "Võ Anh Thư",
  ZVAY000000000001: "Bùi Hải Yến",
}

export function looksLikeAccount(value: string): boolean {
  return /^[A-Z]{0,6}\d{6,}$/.test(value.replace(/\s/g, "").toUpperCase())
}

/** Tên người nhận. Không trả số tài khoản thô. */
export function payeeName(name?: string | null, accountId?: string | null): string {
  const id = (accountId ?? "").replace(/\s/g, "").toUpperCase()
  const seeded = DEMO_PAYEES[id]
  const shown = displayHolder(name)
  if (!shown || looksLikeAccount(shown)) return seeded ?? ""
  return shown
}

export function givenName(full: string): string {
  const parts = full.trim().split(/\s+/).filter(Boolean)
  return parts[parts.length - 1] ?? full
}

const COMPANY_MARK = ["CONG TY", "TNHH", "CHU DAU", "CO PHAN", "CTCP", "CTY "]
const COMPANY_PREFIX = ["CONG TY TNHH MTV", "CONG TY CO PHAN", "CONG TY TNHH", "CONG TY", "CTY TNHH", "CTCP", "CHU DAU TU", "TNHH"]

function titleWord(word: string): string {
  const chars = [...word]
  const first = chars[0]
  if (!first) return ""
  return first.toLocaleUpperCase("vi") + chars.slice(1).join("").toLocaleLowerCase("vi")
}

function isCompany(full: string): boolean {
  const ascii = nameAscii(full)
  return COMPANY_MARK.some((mark) => ascii.includes(mark))
}

function meaningfulOrgWords(full: string): string[] {
  const original = full.trim().split(/\s+/).filter(Boolean)
  const ascii = original.map((word) => nameAscii(word))
  const prefixes = [...COMPANY_PREFIX].sort((left, right) => right.length - left.length)
  let index = 0
  let advanced = true
  while (advanced && index < original.length) {
    advanced = false
    const rest = ascii.slice(index).join(" ")
    for (const prefix of prefixes) {
      if (rest === prefix || rest.startsWith(`${prefix} `)) {
        index += prefix.split(" ").length
        advanced = true
        break
      }
    }
  }
  return original.slice(index)
}

const WORD: Record<string, string> = {
  NGUYEN: "Nguyễn", HUE: "Huệ", LE: "Lê", QUY: "Quý", DON: "Đôn", TRAN: "Trần", PHU: "Phú",
  VAN: "Văn", AN: "An", TRAI: "Trãi", HONG: "Hồng", PHONG: "Phong", QUANG: "Quang", TRUNG: "Trung",
  HUNG: "Hùng", VUONG: "Vương", LY: "Lý", TU: "Tự", TRONG: "Trọng", PHAN: "Phan", CHAU: "Châu",
  TRINH: "Trinh", MINH: "Minh", PHAT: "Phát", HOANG: "Hoàng", GIA: "Gia", THANH: "Thành", CONG: "Công",
  KIM: "Kim", OANH: "Oanh", NAM: "Nam", VIET: "Việt", DONG: "Đông", BAC: "Bắc", SON: "Sơn",
  PHUONG: "Phương", BINH: "Bình", THINH: "Thịnh", THAI: "Thái", HOA: "Hoa", MAI: "Mai", TINH: "Tinh",
  LANG: "Lang", HA: "Hà", DO: "Đô", TAY: "Tây", MEKONG: "Mekong", TRUONG: "Trường",
}

function titled(word: string): string {
  return WORD[nameAscii(word)] ?? titleWord(word)
}

function chipBase(full: string): { label: string; familyInitial: string } {
  const words = full.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return { label: "", familyInitial: "" }
  if (isCompany(full)) {
    const meaningful = meaningfulOrgWords(full)
    const tail = (meaningful.length > 0 ? meaningful : words).slice(-2)
    const label = tail.map(titled).join(" ")
    const family = meaningful[0] ?? words[0] ?? ""
    return { label, familyInitial: [...titled(family)][0] ?? "" }
  }
  const given = words[words.length - 1] ?? ""
  const family = words[0] ?? ""
  return { label: titleWord(given), familyInitial: [...titleWord(family)][0] ?? "" }
}

export type RecentChip = { id: string; label: string; letter: string }

/** Người: tên gọi. Tổ chức: hai từ cuối. Trùng tên thì thêm họ. */
export function recentChips(items: { id: string; name: string }[]): RecentChip[] {
  const seen = new Set<string>()
  const unique = items.filter((item) => {
    if (!item.id || seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })
  const bases = unique.map((item) => ({ ...item, ...chipBase(item.name) }))
  const counts = new Map<string, number>()
  for (const item of bases) {
    const key = item.label.toLocaleLowerCase("vi")
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return bases.map((item) => {
    const collide = (counts.get(item.label.toLocaleLowerCase("vi")) ?? 0) > 1
    const label = collide && item.familyInitial ? `${item.label} ${item.familyInitial}.` : item.label
    return { id: item.id, label, letter: [...label][0] ?? "" }
  })
}

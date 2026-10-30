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

import { personas } from "./personas"

const MARKS = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
const ASCII = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyydAAAAAAAAAAAAAAAAAEEEEEEEEEEEIIIIIOOOOOOOOOOOOOOOOOUUUUUUUUUUUYYYYYD"

function nameAscii(value: string): string {
  let out = ""
  for (const ch of value) {
    const index = MARKS.indexOf(ch)
    out += index >= 0 ? ASCII[index] : ch
  }
  return out.toUpperCase()
}

const byAscii = new Map(personas.map((person) => [nameAscii(person.full_name), person.full_name]))

export function displayHolder(holder: string | null | undefined): string {
  if (!holder) return ""
  return byAscii.get(holder.trim().toUpperCase()) ?? holder
}

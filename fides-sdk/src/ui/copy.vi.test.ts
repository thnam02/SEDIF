import assert from "node:assert/strict"
import test from "node:test"

import { copyVi } from "./copy.vi.ts"

function strings(value: unknown, path: string): { path: string; text: string }[] {
  if (typeof value === "string") return [{ path, text: value }]
  if (Array.isArray(value)) return value.flatMap((item, index) => strings(item, `${path}[${index}]`))
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) => strings(item, path ? `${path}.${key}` : key))
  }
  return []
}

test("chỉ câu simo_listed được chứa chữ lừa đảo", () => {
  const hits = strings(copyVi, "").filter((item) => item.text.includes("lừa đảo"))
  assert.deepEqual(hits.map((item) => item.path), ["simo"])
})

test("các câu bắt buộc giữ nguyên", () => {
  assert.equal(
    copyVi.simo,
    "Tài khoản nhận đã bị báo cáo có dấu hiệu liên quan đến lừa đảo. Cơ quan công an và ngân hàng không bao giờ yêu cầu chuyển tiền vào tài khoản 'tạm giữ' hay 'xác minh'. Nếu ai đó đang hướng dẫn bạn chuyển khoản, hãy dừng lại.",
  )
  assert.equal(copyVi.hangup.title, "Hãy cúp máy trước khi tiếp tục")
  assert.equal(
    copyVi.hangup.body,
    "Công an, toà án và ngân hàng không bao giờ yêu cầu chuyển tiền qua điện thoại. Kẻ gian thường dặn bạn không được cúp máy.",
  )
  assert.equal(
    copyVi.countdown.body,
    "Hãy dành 10 phút để suy nghĩ hoặc gọi cho người thân. Nếu có cuộc gọi mới, thời gian sẽ bắt đầu lại.",
  )
  assert.equal(copyVi.pause.question, "Ai đang hướng dẫn bạn chuyển khoản này?")
  assert.equal(copyVi.guides.length, 7)
  assert.equal(
    copyVi.permission.body,
    "FIDES chỉ kiểm tra bạn CÓ đang trong cuộc gọi hay không khi bạn chuyển tiền. Chúng tôi không nghe, không ghi âm, không đọc số điện thoại hay tin nhắn.",
  )
})

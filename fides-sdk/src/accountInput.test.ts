import assert from "node:assert/strict"
import test from "node:test"

import { classifyAccountInput } from "./accountInput.ts"

test("gõ đều là typed", () => {
  assert.equal(
    classifyAccountInput([
      { type: "key", t: 0 },
      { type: "key", t: 400 },
      { type: "key", t: 900 },
    ]),
    "typed",
  )
})

test("nghỉ hơn 1,5s là typed_chunked", () => {
  assert.equal(
    classifyAccountInput([
      { type: "key", t: 0 },
      { type: "key", t: 200 },
      { type: "key", t: 1_701 },
    ]),
    "typed_chunked",
  )
})

test("dán một lần là pasted", () => {
  assert.equal(classifyAccountInput([{ type: "paste", t: 0 }]), "pasted")
})

test("chọn từ danh bạ thụ hưởng là chosen", () => {
  assert.equal(classifyAccountInput([{ type: "choose", t: 50 }]), "chosen")
})

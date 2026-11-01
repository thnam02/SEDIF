import assert from "node:assert/strict"
import test from "node:test"

import { LEVEL1_EXPIRED, level1ConfirmRejected, level1OnLeave } from "./level1Leave.ts"

test("leaving the level-1 sheet cancels and drops the idempotency key", () => {
  for (const event of ["navigation", "background", "unmount"] as const) {
    assert.deepEqual(level1OnLeave(event), { request: "cancel", forgetKey: true })
  }
  assert.deepEqual(level1OnLeave("confirm"), { request: "none", forgetKey: false })
})

test("a 409 on confirm shows the bank message and returns to the form", () => {
  assert.deepEqual(level1ConfirmRejected(409, LEVEL1_EXPIRED), {
    back: true,
    message: LEVEL1_EXPIRED,
    forgetKey: true,
  })
  assert.equal(level1ConfirmRejected(500, LEVEL1_EXPIRED), null)
})

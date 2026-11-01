import assert from "node:assert/strict"
import test from "node:test"

import { addMethod } from "./addMethod.ts"

test("every transfer path produces an add_method", () => {
  assert.equal(addMethod("new_typed"), "typed")
  assert.equal(addMethod("new_pasted", "paste"), "paste")
  assert.equal(addMethod("new_pasted"), "paste")
  assert.equal(addMethod("qr"), "qr")
  assert.equal(addMethod("saved_payee"), "contact")
  assert.equal(addMethod("new_typed", "image_capture"), "paste")
  assert.equal(addMethod("new_typed", "image_upload"), "paste")
})

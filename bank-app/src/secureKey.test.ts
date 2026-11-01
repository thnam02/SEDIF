import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import test from "node:test"

import { idempotencyStoreKey, secureStoreKey } from "./secureKey.ts"
import { payeeGate } from "./payeeLookup.ts"

const LEGAL = /^[A-Za-z0-9._-]+$/

test("idempotency key is a hash even with a Vietnamese name, spaces and an unknown payee", () => {
  const key = secureStoreKey("idem_", ["Huỳnh Thị Mận", "0896 895 239", "23000000"])
  const expected = `idem_${createHash("sha256").update("Huỳnh Thị Mận|0896 895 239|23000000").digest("hex")}`
  assert.equal(key, expected)
  assert.match(key, LEGAL)
  assert.equal(key.includes(" "), false)
  assert.equal(key.includes(":"), false)
  assert.equal(key.includes("Huỳnh"), false)
  assert.equal(key.includes("0896"), false)
  assert.equal(key, idempotencyStoreKey("Huỳnh Thị Mận", "0896 895 239", 23000000))
  assert.notEqual(key, idempotencyStoreKey("Huỳnh Thị Mận", "0896 895 239", 23000001))
  assert.notEqual(key, secureStoreKey("face_", ["Lý Minh Châu", "0896 895 239"]))
})

test("unknown account cannot continue and the placeholder is not a name", () => {
  const unknown = payeeGate("0896 895 239", "missing", "")
  assert.equal(unknown.canContinue, false)
  assert.equal(unknown.showMissing, true)
  const placeholder = payeeGate("0896895239", "found", "Người nhận")
  assert.equal(placeholder.canContinue, false)
  assert.equal(placeholder.showMissing, false)
  const named = payeeGate("0902230230", "found", "Lý Minh Châu")
  assert.equal(named.canContinue, true)
  assert.equal(named.showMissing, false)
  const partial = payeeGate("0896", "missing", "")
  assert.equal(partial.canContinue, false)
  assert.equal(partial.showMissing, false)
})

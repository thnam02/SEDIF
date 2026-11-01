import assert from "node:assert/strict"
import test from "node:test"

import { deviceState } from "./deviceState.ts"

test("iOS capture and the demo toggle set device.state", () => {
  assert.equal(deviceState(true, false), "active_capture")
  assert.equal(deviceState(false, false), "none")
  assert.equal(deviceState(null, false), "unknown")
  assert.equal(deviceState(false, true), "active_capture")
  assert.equal(deviceState(null, true), "active_capture")
})

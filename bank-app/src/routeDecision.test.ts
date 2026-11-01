import assert from "node:assert/strict"
import test from "node:test"

import { confirmOutcome, resultRoute } from "./routeDecision.ts"

test("each numeric level opens one screen", () => {
  assert.equal(resultRoute(0, "none"), "success")
  assert.equal(resultRoute(1, "banner"), "success_reminder")
  assert.equal(resultRoute(2, "three_button"), "three_button")
  assert.equal(resultRoute(3, "security_workflow"), "security")
})

test("level 1 waits on a reminder before the money moves", () => {
  assert.equal(confirmOutcome(0), "settle")
  assert.equal(confirmOutcome(1), "reminder")
  assert.equal(confirmOutcome(2), "hold")
  assert.equal(confirmOutcome(3), "hold")
})

test("each intervention name opens its screen", () => {
  assert.equal(resultRoute(0, "none"), "success")
  assert.equal(resultRoute(0, "reminder"), "success_reminder")
  assert.equal(resultRoute(0, "three_button"), "three_button")
  assert.equal(resultRoute(0, "security"), "security")
})

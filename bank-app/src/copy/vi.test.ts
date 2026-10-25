import assert from "node:assert/strict"
import test from "node:test"

import { copy } from "./vi.ts"

function strings(value: unknown): string[] {
  if (typeof value === "string") return [value]
  if (Array.isArray(value)) return value.flatMap(strings)
  if (value && typeof value === "object") return Object.values(value).flatMap(strings)
  return []
}

test("copy ngân hàng không chứa chữ lừa đảo", () => {
  const hits = strings(copy).filter((item) => item.includes("lừa đảo"))
  assert.deepEqual(hits, [])
})

import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"

import { capPrimary, fidesTheme } from "./theme.ts"

const preset = createRequire(import.meta.url)("../../tailwind.preset.cjs") as {
  theme: { extend: { colors: Record<string, string>; backgroundImage: { gradient: string }; borderRadius: { phone: string }; fontSize: { "phone-sm": [string, { lineHeight: string }] } } }
}

test("preset NativeWind khớp theme và tailwind v0", () => {
  const colors = preset.theme.extend.colors
  assert.equal(colors.navy, "#0B2A4A")
  assert.equal(colors.deep, "#0A4D8C")
  assert.equal(colors.mint, "#E6FAF8")
  assert.equal(colors.aqua, "#5FE3D6")
  assert.equal(colors.warn, "#F2994A")
  assert.equal(colors.danger, "#D64545")
  assert.equal(colors.blue, "#0E7FB0")
  assert.equal(colors.money, colors.deep)
  assert.equal(colors.money, fidesTheme.colors.money)
  assert.equal(colors.warnSoft, fidesTheme.colors.warnSoft)
  assert.equal(colors.dangerSoft, fidesTheme.colors.dangerSoft)
  assert.equal(colors.gradientFrom, "#3CC6E8")
  assert.equal(colors.gradientTo, "#0E7FB0")
  assert.equal(preset.theme.extend.backgroundImage.gradient, "linear-gradient(135deg, #3CC6E8, #0E7FB0)")
  assert.equal(preset.theme.extend.borderRadius.phone, "38px")
  assert.equal(fidesTheme.radius.phone, 38)
  assert.deepEqual(preset.theme.extend.fontSize["phone-sm"], ["14px", { lineHeight: "20px" }])
  assert.equal(fidesTheme.type.body, 14)
  assert.equal(fidesTheme.type.line, 20)
})

test("chữ lớn chỉ giữ hai nút chính", () => {
  assert.deepEqual(capPrimary(["a", "b", "c"], true), ["a", "b"])
  assert.deepEqual(capPrimary(["a", "b", "c"], false), ["a", "b", "c"])
})

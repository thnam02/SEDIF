/** Màu và cỡ chữ lấy từ design-ref/v0-fides/tailwind.config.ts. Không đổi các giá trị đó. */

export const fidesTheme = {
  colors: {
    navy: "#0B2A4A",
    deep: "#0A4D8C",
    mint: "#E6FAF8",
    aqua: "#5FE3D6",
    warn: "#F2994A",
    danger: "#D64545",
    blue: "#0E7FB0",
    warnSoft: "#FFF4E7",
    dangerSoft: "#FFF0F0",
    money: "#0A4D8C",
    white: "#FFFFFF",
    gradientFrom: "#3CC6E8",
    gradientTo: "#0E7FB0",
  },
  radius: { phone: 38, card: 16, button: 12 },
  type: { title: 20, body: 14, small: 14, countdown: 48, amount: 26, line: 20 },
} as const

export type FidesTheme = typeof fidesTheme
export type FidesThemeOverride = Partial<{ [K in keyof FidesTheme]: Partial<FidesTheme[K]> }>

export function mergeFidesTheme(theme?: FidesThemeOverride, largeText = false) {
  const scale = largeText ? 1.4 : 1
  const type = { ...fidesTheme.type, ...(theme?.type ?? {}) }
  return {
    ...fidesTheme,
    colors: { ...fidesTheme.colors, ...(theme?.colors ?? {}) },
    radius: { ...fidesTheme.radius, ...(theme?.radius ?? {}) },
    type: {
      title: type.title * scale,
      body: type.body * scale,
      small: type.small * scale,
      countdown: type.countdown * scale,
      amount: type.amount * scale,
      line: type.line * scale,
    },
  }
}

export type ResolvedTheme = ReturnType<typeof mergeFidesTheme>

export function capPrimary<T>(items: T[], largeText: boolean): T[] {
  return largeText ? items.slice(0, 2) : items
}

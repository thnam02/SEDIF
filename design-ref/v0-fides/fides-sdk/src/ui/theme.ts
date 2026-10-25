export const fidesTheme = {
  colors: {
    navy: '#0B2A4A', ink: '#153B59', money: '#0A4D8C', mint: '#E6FAF8', accent: '#5FE3D6', cyan: '#3CC6E8', blue: '#0E7FB0', orange: '#F2994A', orangeSoft: '#FFF4E7', danger: '#D64545', dangerSoft: '#FFF0F0', white: '#FFFFFF', muted: '#62788A', border: '#D7E4E8',
  },
  radius: { card: 20, button: 16, pill: 999 },
  type: { title: 30, heading: 25, body: 16, small: 13, amount: 26 },
} as const

export type FidesTheme = typeof fidesTheme
export type FidesThemeOverride = Partial<{ [K in keyof FidesTheme]: Partial<FidesTheme[K]> }>

export function mergeFidesTheme(theme?: FidesThemeOverride, largeText = false) {
  const scale = largeText ? 1.4 : 1
  return {
    ...fidesTheme,
    colors: { ...fidesTheme.colors, ...(theme?.colors ?? {}) },
    radius: { ...fidesTheme.radius, ...(theme?.radius ?? {}) },
    type: { ...fidesTheme.type, ...(theme?.type ?? {}), title: fidesTheme.type.title * scale, heading: fidesTheme.type.heading * scale, body: fidesTheme.type.body * scale, small: fidesTheme.type.small * scale, amount: fidesTheme.type.amount * scale },
  }
}

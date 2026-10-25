import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: { extend: { colors: { navy: '#0B2A4A', deep: '#0A4D8C', mint: '#E6FAF8', aqua: '#5FE3D6', warn: '#F2994A', danger: '#D64545' }, backgroundImage: { gradient: 'linear-gradient(135deg, #3CC6E8, #0E7FB0)' }, borderRadius: { phone: '38px' }, fontSize: { 'phone-sm': ['14px', { lineHeight: '20px' }] } } },
  plugins: [],
}
export default config

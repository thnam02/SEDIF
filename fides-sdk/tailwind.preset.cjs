/** Preset NativeWind. Cùng giá trị với src/ui/theme.ts. */
module.exports = {
  theme: {
    extend: {
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
      backgroundImage: { gradient: "linear-gradient(135deg, #3CC6E8, #0E7FB0)" },
      borderRadius: { phone: "38px" },
      fontSize: { "phone-sm": ["14px", { lineHeight: "20px" }] },
    },
  },
}

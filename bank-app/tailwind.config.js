const preset = require("fides-sdk/tailwind.preset")

module.exports = {
  presets: [preset],
  content: ["./App.tsx", "./app/**/*.{js,jsx,ts,tsx}", "./src/**/*.{js,jsx,ts,tsx}"],
}

module.exports = {
  preset: "react-native",
  testMatch: ["<rootDir>/src/ui/**/*.test.tsx"],
  setupFiles: ["<rootDir>/jest.setup.cjs"],
  transformIgnorePatterns: [
    "node_modules/(?!(react-native|@react-native|@testing-library|lucide-react-native|react-native-reanimated|react-native-svg)/)",
  ],
}

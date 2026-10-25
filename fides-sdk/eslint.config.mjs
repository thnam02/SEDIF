import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["src/generated/**", "node_modules/**"] },
  ...tseslint.configs.recommended,
)

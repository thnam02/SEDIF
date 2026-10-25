import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["src/api/openapi.ts", "node_modules/**"] },
  ...tseslint.configs.recommended,
)

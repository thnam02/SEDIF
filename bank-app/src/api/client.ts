import createClient from "openapi-fetch"

import type { paths } from "./openapi"

export const BANK_URL = process.env.EXPO_PUBLIC_BANK_URL ?? "http://127.0.0.1:8092"

export const bank = createClient<paths>({ baseUrl: BANK_URL })

export async function unwrap<T>(pending: Promise<{ data?: T; error?: { detail?: string } }>): Promise<T> {
  const result = await pending
  if (result.data === undefined) {
    throw new Error(result.error?.detail ?? "ngân hàng không trả lời")
  }
  return result.data
}

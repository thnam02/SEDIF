import Constants from "expo-constants"
import { Platform } from "react-native"
import createClient from "openapi-fetch"

import type { paths } from "./openapi"

const CONFIGURED = process.env.EXPO_PUBLIC_BANK_URL ?? "http://127.0.0.1:8082"

/** 10.0.2.2 chỉ tới được từ giả lập Android. iPhone dùng máy đang chạy Metro, giữ nguyên cổng. */
export function resolveBankUrl(configured: string, platform: string, packagerHost: string | null | undefined): string {
  let url: URL
  try {
    url = new URL(configured)
  } catch {
    return configured
  }
  const local = url.hostname === "10.0.2.2" || url.hostname === "127.0.0.1" || url.hostname === "localhost"
  if (!local || platform === "android") return configured
  const host = packagerHost?.split(":")[0]
  const packagerIsDevice = Boolean(host && host !== "localhost" && host !== "127.0.0.1" && host !== "10.0.2.2")
  if (packagerIsDevice) {
    url.hostname = host as string
    return url.toString().replace(/\/$/, "")
  }
  if (url.hostname === "10.0.2.2") {
    url.hostname = "127.0.0.1"
    return url.toString().replace(/\/$/, "")
  }
  return configured
}

export const BANK_URL = resolveBankUrl(CONFIGURED, Platform.OS, Constants.expoConfig?.hostUri)

export const bank = createClient<paths>({ baseUrl: BANK_URL })

export async function unwrap<T>(pending: Promise<{ data?: T; error?: { detail?: string } }>): Promise<T> {
  const result = await pending
  if (result.data === undefined) {
    throw new Error(result.error?.detail ?? "ngân hàng không trả lời")
  }
  return result.data
}

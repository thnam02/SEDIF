import * as SecureStore from "expo-secure-store"

import { idempotencyStoreKey, secureStoreKey } from "../secureKey"

export async function transferKey(from: string, to: string, amount: number): Promise<string> {
  const name = idempotencyStoreKey(from, to, amount)
  const found = await SecureStore.getItemAsync(name)
  if (found) return found
  const created = secureStoreKey("tok_", [from, to, String(amount), String(Date.now())])
  await SecureStore.setItemAsync(name, created)
  return created
}

export async function forgetTransferKey(from: string, to: string, amount: number): Promise<void> {
  await SecureStore.deleteItemAsync(idempotencyStoreKey(from, to, amount))
}

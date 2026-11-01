import * as SecureStore from "expo-secure-store"

export async function transferKey(from: string, to: string, amount: number): Promise<string> {
  const name = `idem-${from}-${to}-${amount}`.slice(0, 120)
  const found = await SecureStore.getItemAsync(name)
  if (found) return found
  const created = `${from}-${to}-${amount}-${Date.now()}`
  await SecureStore.setItemAsync(name, created)
  return created
}

export async function forgetTransferKey(from: string, to: string, amount: number): Promise<void> {
  const name = `idem-${from}-${to}-${amount}`.slice(0, 120)
  await SecureStore.deleteItemAsync(name)
}

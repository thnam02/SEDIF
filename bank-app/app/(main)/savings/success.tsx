import { useLocalSearchParams } from "expo-router"

import { SavingsSuccess } from "../../../src/screens/Products"

export default function Page() {
  const { amount } = useLocalSearchParams<{ amount?: string }>()
  return <SavingsSuccess amount={amount ?? "0"} />
}

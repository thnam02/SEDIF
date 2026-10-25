import { useLocalSearchParams } from "expo-router"

import { LoanSuccess } from "../../../src/screens/Products"

export default function Page() {
  const { amount } = useLocalSearchParams<{ amount?: string }>()
  return <LoanSuccess amount={amount ?? "0"} />
}

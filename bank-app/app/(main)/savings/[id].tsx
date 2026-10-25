import { useLocalSearchParams } from "expo-router"

import { SavingsDetail } from "../../../src/screens/Products"

export default function Page() {
  const { id } = useLocalSearchParams<{ id: string }>()
  return <SavingsDetail id={id ?? ""} />
}

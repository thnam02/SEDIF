import { useEffect } from "react"
import { useRouter } from "expo-router"

import { openShopQr } from "../../../src/screens/Transfer"

export default function ScanTab() {
  const router = useRouter()
  useEffect(() => {
    openShopQr(router)
  }, [router])
  return null
}

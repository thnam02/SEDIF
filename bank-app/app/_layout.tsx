import { useEffect } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Stack } from "expo-router"
import { SafeAreaProvider } from "react-native-safe-area-context"

import { BUILD } from "../src/dev/buildInfo"
import { DemoDrawer } from "../src/dev/DemoDrawer"

const client = new QueryClient()

export default function RootLayout() {
  useEffect(() => {
    console.log(`BVC bundle ${BUILD.commit}`)
  }, [])
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>
        <Stack screenOptions={{ headerShown: false }} />
        <DemoDrawer />
      </QueryClientProvider>
    </SafeAreaProvider>
  )
}

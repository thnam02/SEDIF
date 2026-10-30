import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Stack } from "expo-router"
import { SafeAreaProvider } from "react-native-safe-area-context"

import { DemoDrawer } from "../src/dev/DemoDrawer"

const client = new QueryClient()

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>
        <Stack screenOptions={{ headerShown: false }} />
        <DemoDrawer />
      </QueryClientProvider>
    </SafeAreaProvider>
  )
}

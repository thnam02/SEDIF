import type { ReactNode } from "react"
import { Pressable, ScrollView, Text, View } from "react-native"
import { useRouter } from "expo-router"
import { ArrowLeft } from "lucide-react-native"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { fidesTheme } from "fides-sdk"

import { copy } from "../copy/vi"
import { useBank } from "../store"

export const colors = fidesTheme.colors

export function useType() {
  const large = useBank((state) => state.largeText)
  const scale = large ? 1.4 : 1
  return { title: 24 * scale, body: 14 * scale, small: 12 * scale }
}

export function Screen({ children }: { children: ReactNode }) {
  const insets = useSafeAreaInsets()
  return (
    <View style={{ flex: 1, backgroundColor: colors.white, paddingTop: insets.top }}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: 20, paddingHorizontal: 20, paddingTop: 24, paddingBottom: insets.bottom + 24 }}>
        {children}
      </ScrollView>
    </View>
  )
}

export function Header({ title }: { title: string }) {
  const router = useRouter()
  const type = useType()
  return (
    <View className="flex-row items-center gap-3" style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
      {router.canGoBack() ? (
        <Pressable accessibilityRole="button" accessibilityLabel={copy.home} onPress={() => router.back()}>
          <ArrowLeft size={19} color={colors.deep} />
        </Pressable>
      ) : null}
      <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{title}</Text>
    </View>
  )
}

export function Card({ children }: { children: ReactNode }) {
  return <View className="flex-col gap-3 rounded-2xl bg-mint p-4" style={{ gap: 12, borderRadius: 16, backgroundColor: colors.mint, padding: 16 }}>{children}</View>
}

export function Button({ label, onPress, outline = false, testID }: { label: string; onPress?: () => void; outline?: boolean; testID?: string }) {
  const type = useType()
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      onPress={onPress}
      className="min-h-11 items-center justify-center rounded-xl px-4"
      style={{ minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 12, paddingHorizontal: 16, backgroundColor: outline ? colors.white : colors.deep, borderWidth: outline ? 1 : 0, borderColor: colors.deep }}
    >
      <Text style={{ color: outline ? colors.deep : colors.white, fontSize: type.body, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  )
}

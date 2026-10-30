import { type ReactNode } from "react"
import { Pressable, ScrollView, Text, View } from "react-native"
import { useRouter } from "expo-router"
import { ArrowLeft, Bell, Home, ShieldCheck } from "lucide-react-native"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { FidesCallIndicator, fidesTheme } from "fides-sdk"

import { copy } from "../copy/vi"
import { openDemoDrawer } from "../dev/DemoDrawer"
import { DEMO, useBank } from "../store"

export const colors = fidesTheme.colors

export function useType() {
  const large = useBank((state) => state.largeText)
  const scale = large ? 1.4 : 1
  return { title: 24 * scale, body: 14 * scale, small: 12 * scale }
}

export function Screen({ children, call = false, footer }: { children: ReactNode; call?: boolean; footer?: ReactNode }) {
  const insets = useSafeAreaInsets()
  return (
    <View style={{ flex: 1, backgroundColor: colors.white, paddingTop: insets.top }}>
      <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentInsetAdjustmentBehavior="never" contentContainerStyle={{ gap: 20, paddingHorizontal: 20, paddingTop: 24, paddingBottom: footer ? 240 : insets.bottom + 24 }}>
        {call ? <FidesCallIndicator /> : null}
        {children}
      </ScrollView>
      {footer ? <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: insets.bottom + 12, backgroundColor: colors.white }}>{footer}</View> : null}
    </View>
  )
}

const round = { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.mint, alignItems: "center" as const, justifyContent: "center" as const }

export function Header({ title, subtitle, tools = false, shield = true }: { title: string; subtitle?: string; tools?: boolean; shield?: boolean }) {
  const router = useRouter()
  const type = useType()
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
      {router.canGoBack() ? (
        <Pressable accessibilityRole="button" accessibilityLabel={copy.back} onPress={() => router.back()}>
          <ArrowLeft size={19} color={colors.deep} />
        </Pressable>
      ) : null}
      <View style={{ flex: 1 }}>
        <Text numberOfLines={2} style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{title}</Text>
        {subtitle ? <Text style={{ color: "#64748B", fontSize: type.small }}>{subtitle}</Text> : null}
      </View>
      {tools ? (
        <View style={{ flexDirection: "row", gap: 8 }}>
          {DEMO && shield ? (
            <Pressable testID="demo-drawer" accessibilityRole="button" accessibilityLabel="Phiên FIDES" onPress={openDemoDrawer} onLongPress={openDemoDrawer} style={round}>
              <ShieldCheck size={16} color={colors.deep} />
            </Pressable>
          ) : null}
          <Pressable testID="header-notifications" accessibilityRole="button" onPress={() => router.push("/(main)/history")} style={round}>
            <Bell size={16} color={colors.deep} />
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.aqua, position: "absolute", top: 6, right: 6 }} />
          </Pressable>
          <Pressable testID="header-home" accessibilityRole="button" accessibilityLabel={copy.home} onPress={() => router.replace("/(main)/(tabs)")} style={round}>
            <Home size={16} color={colors.deep} />
          </Pressable>
        </View>
      ) : null}
    </View>
  )
}

export function Card({ children }: { children: ReactNode }) {
  return <View className="flex-col gap-3 rounded-2xl bg-mint p-4" style={{ gap: 12, borderRadius: 16, backgroundColor: colors.mint, padding: 16 }}>{children}</View>
}

export function Button({ label, onPress, outline = false, testID, disabled = false }: { label: string; onPress?: () => void; outline?: boolean; testID?: string; disabled?: boolean }) {
  const type = useType()
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={disabled ? undefined : onPress}
      className="min-h-11 items-center justify-center rounded-xl px-4"
      style={{ minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 12, paddingHorizontal: 16, backgroundColor: outline ? colors.white : colors.deep, borderWidth: outline ? 1 : 0, borderColor: colors.deep, opacity: disabled ? 0.4 : 1 }}
    >
      <Text style={{ color: outline ? colors.deep : colors.white, fontSize: type.body, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  )
}

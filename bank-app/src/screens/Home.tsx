import { useState } from "react"
import { Pressable, Text, View } from "react-native"
import { useRouter } from "expo-router"
import { ArrowRight, CreditCard, Eye, EyeOff, HandCoins, PiggyBank, ShieldCheck, UserRound } from "lucide-react-native"

import { copy } from "../copy/vi"
import { statement, vnd, when } from "../format"
import { personas } from "../personas"
import { useHistory, useAccount } from "../api/queries"
import { DEMO, useBank } from "../store"
import { JudgePanel } from "../judge/JudgePanel"
import { colors, Screen, useType } from "../ui/parts"

const icons = [ArrowRight, PiggyBank, HandCoins, CreditCard]
const routes = ["/(main)/transfer/recipient", "/(main)/savings", "/(main)/(tabs)/loans", "/(main)/card"] as const

export function HomeScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const role = useBank((state) => state.role)
  const account = useAccount(accountId)
  const history = useHistory(accountId)
  const [hidden, setHidden] = useState(false)
  const type = useType()
  const person = personas.find((item) => item.role === role)
  const recent = statement(history.data?.entries ?? [], account.data?.balance ?? 0).slice(0, 3)
  return (
    <Screen>
      <JudgePanel />
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <View>
          <Text style={{ color: "#64748B", fontSize: type.body }}>{copy.hello},</Text>
          <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{person?.full_name}</Text>
        </View>
        <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
          <UserRound size={19} color={colors.deep} />
        </View>
      </View>
      {DEMO ? (
        <Pressable accessibilityRole="button" onPress={() => router.push("/dev")}>
          <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>Phiên FIDES</Text>
        </Pressable>
      ) : null}
      <View className="rounded-3xl bg-deep p-5" style={{ borderRadius: 24, backgroundColor: colors.deep, padding: 20, gap: 16 }}>
        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
          <Text style={{ color: colors.white, fontSize: type.small }}>{copy.balance}</Text>
          <Pressable accessibilityRole="button" onPress={() => setHidden(!hidden)}>{hidden ? <EyeOff size={16} color={colors.white} /> : <Eye size={16} color={colors.white} />}</Pressable>
        </View>
        <Text style={{ color: colors.white, fontSize: 30, fontWeight: "700" }}>{hidden ? "••••••••" : vnd(account.data?.balance ?? 0)}</Text>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <ShieldCheck size={14} color={colors.aqua} />
          <Text style={{ color: colors.aqua, fontSize: type.small }}>{copy.protected}</Text>
        </View>
      </View>
      <View style={{ flexDirection: "row", gap: 8 }}>
        {copy.quick.map((name, index) => {
          const Icon = icons[index] ?? ArrowRight
          return (
            <Pressable key={name} accessibilityRole="button" onPress={() => router.push(routes[index] ?? "/(main)/(tabs)")} style={{ flex: 1, alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.mint, padding: 12 }}>
              <Icon size={18} color={colors.deep} />
              <Text style={{ color: colors.deep, fontSize: 11 }}>{name}</Text>
            </Pressable>
          )
        })}
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.recent}</Text>
        <Pressable accessibilityRole="button" onPress={() => router.push("/(main)/history")}>
          <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>{copy.seeAll}</Text>
        </Pressable>
      </View>
      {recent.map((item) => {
        const stamp = when(item.ts)
        const inbound = item.direction === "in"
        return (
          <View key={item.tx_id} style={{ flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12 }}>
            <View>
              <Text style={{ color: colors.navy, fontWeight: "600", fontSize: type.body }}>{item.purpose_text || item.tx_type}</Text>
              <Text style={{ color: "#64748B", fontSize: type.small }}>{item.tx_type}</Text>
            </View>
            <View>
              <Text style={{ color: inbound ? "#2E7D32" : colors.navy, fontWeight: "700", textAlign: "right" }}>{inbound ? "+" : "−"}{vnd(item.amount)}</Text>
              <Text style={{ color: "#94A3B8", fontSize: type.small, textAlign: "right" }}>{stamp.time} · {stamp.date}</Text>
              <Text style={{ color: "#64748B", fontSize: type.small, textAlign: "right" }}>{copy.balanceAfter} {vnd(item.balanceAfter)}</Text>
            </View>
          </View>
        )
      })}
    </Screen>
  )
}

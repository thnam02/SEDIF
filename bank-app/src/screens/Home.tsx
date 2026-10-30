import { useState, type ReactNode } from "react"
import { Pressable, ScrollView, Text, useWindowDimensions, View } from "react-native"
import { useRouter } from "expo-router"
import { ArrowRight, Bell, ChevronDown, ChevronUp, CreditCard, Eye, EyeOff, HandCoins, Menu, PiggyBank, Receipt, Search, ShieldCheck, ShoppingBag, Smartphone, UserRound } from "lucide-react-native"

import { useAccount, useHistory } from "../api/queries"
import { copy } from "../copy/vi"
import { movementTitle, statement, vnd, when } from "../format"
import { displayHolder } from "../names"
import { personas } from "../personas"
import { DEMO, useBank } from "../store"
import { JudgePanel } from "../judge/JudgePanel"
import { colors, Screen, useType } from "../ui/parts"

const quickIcons = [ArrowRight, Smartphone, PiggyBank, HandCoins]
const moreIcons = [CreditCard, Receipt, ArrowRight]
const serviceIcons = [ShoppingBag, Smartphone, HandCoins, CreditCard]

export function HomeScreen() {
  const router = useRouter()
  const width = useWindowDimensions().width
  const accountId = useBank((state) => state.accountId)
  const setAccount = useBank((state) => state.setAccount)
  const role = useBank((state) => state.role)
  const [hidden, setHidden] = useState(true)
  const [more, setMore] = useState(false)
  const type = useType()
  const person = personas.find((item) => item.role === role)
  const ids = person?.account_ids ?? (accountId ? [accountId] : [])
  const cardWidth = Math.max(240, width - 40)
  return (
    <Screen>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <JudgePanel />
        <View style={{ flexDirection: "row", gap: 8 }}>
          <RoundIcon testID="home-search" onPress={() => router.push("/(main)/history")}><Search size={16} color={colors.deep} /></RoundIcon>
          <RoundIcon testID="home-notifications" onPress={() => router.push("/(main)/history")}>
            <Bell size={16} color={colors.deep} />
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.aqua, position: "absolute", top: 8, right: 8 }} />
          </RoundIcon>
          <RoundIcon testID="home-menu" onPress={() => router.push("/(main)/(tabs)/profile")}><Menu size={16} color={colors.deep} /></RoundIcon>
        </View>
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700", flex: 1 }}>{copy.hello}, {person?.full_name}</Text>
        <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
          <UserRound size={19} color={colors.deep} />
        </View>
      </View>
      {DEMO ? (
        <Pressable accessibilityRole="button" onPress={() => router.push("/dev")}>
          <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>Phiên FIDES</Text>
        </Pressable>
      ) : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} decelerationRate="fast" snapToInterval={cardWidth + 12} contentContainerStyle={{ gap: 12 }}>
        {ids.map((id) => <BalanceCard key={id} id={id} width={cardWidth} hidden={hidden} selected={id === accountId} onSelect={() => setAccount(id)} onToggle={() => setHidden(!hidden)} />)}
      </ScrollView>
      <View style={{ flexDirection: "row", gap: 8 }}>
        {copy.quick.map((name, index) => {
          const Icon = quickIcons[index] ?? ArrowRight
          const route = ["/(main)/transfer/recipient", "/(main)/topup", "/(main)/savings", "/(main)/(tabs)/loans"][index] ?? "/(main)/(tabs)"
          return (
            <Pressable key={name} testID={`quick-${index}`} accessibilityRole="button" onPress={() => router.push(route)} style={{ flex: 1, alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.mint, padding: 12 }}>
              <Icon size={18} color={colors.deep} />
              <Text style={{ color: colors.deep, fontSize: 11, textAlign: "center" }}>{name}</Text>
            </Pressable>
          )
        })}
        <Pressable testID="quick-more" accessibilityRole="button" onPress={() => setMore(!more)} style={{ width: 44, alignItems: "center", justifyContent: "center", borderRadius: 22, backgroundColor: colors.mint }}>
          {more ? <ChevronUp size={18} color={colors.deep} /> : <ChevronDown size={18} color={colors.deep} />}
        </Pressable>
      </View>
      {more ? (
        <View style={{ flexDirection: "row", gap: 8 }}>
          {copy.quickMore.map((name, index) => {
            const Icon = moreIcons[index] ?? ArrowRight
            const route = ["/(main)/card", "/(main)/bills", "/(main)/transfer/recipient"][index] ?? "/(main)/(tabs)"
            return (
              <Pressable key={name} testID={`quick-extra-${index}`} accessibilityRole="button" onPress={() => router.push(route)} style={{ flex: 1, alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.mint, padding: 12 }}>
                <Icon size={18} color={colors.deep} />
                <Text style={{ color: colors.deep, fontSize: 11, textAlign: "center" }}>{name}</Text>
              </Pressable>
            )
          })}
        </View>
      ) : null}
      <RecentList accountId={accountId} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 12 }} testID="promo-carousel">
        {copy.promos.map((name) => (
          <View key={name} style={{ width: 220, borderRadius: 16, backgroundColor: colors.deep, padding: 16, gap: 8 }}>
            <Text style={{ color: colors.aqua, fontSize: type.small }}>{copy.logo}</Text>
            <Text style={{ color: colors.white, fontWeight: "700", fontSize: type.body }}>{name}</Text>
          </View>
        ))}
      </ScrollView>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.servicesTitle}</Text>
      <View testID="service-grid" style={{ flexDirection: "row", gap: 8 }}>
        {copy.services.map((name, index) => {
          const Icon = serviceIcons[index] ?? ShoppingBag
          return (
            <View key={name} style={{ flex: 1, alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.mint, padding: 12 }}>
              <Icon size={18} color={colors.deep} />
              <Text style={{ color: colors.deep, fontSize: 11, textAlign: "center" }}>{name}</Text>
            </View>
          )
        })}
      </View>
    </Screen>
  )
}

function RoundIcon({ children, onPress, testID }: { children: ReactNode; onPress: () => void; testID: string }) {
  return (
    <Pressable testID={testID} accessibilityRole="button" onPress={onPress} style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
      {children}
    </Pressable>
  )
}

function BalanceCard({ id, width, hidden, selected, onSelect, onToggle }: { id: string; width: number; hidden: boolean; selected: boolean; onSelect: () => void; onToggle: () => void }) {
  const router = useRouter()
  const account = useAccount(id)
  const type = useType()
  const kind = copy.accountKind[account.data?.account_type ?? "personal"] ?? copy.balance
  return (
    <Pressable testID={selected ? "balance-card" : undefined} onPress={onSelect} style={{ width, borderRadius: 24, backgroundColor: colors.deep, padding: 20, gap: 16, borderWidth: selected ? 2 : 0, borderColor: colors.aqua }}>
      <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
        <Text style={{ color: colors.white, fontSize: type.small }}>{kind}</Text>
        <Pressable testID={selected ? "balance-toggle" : undefined} accessibilityRole="button" onPress={onToggle}>{hidden ? <EyeOff size={16} color={colors.white} /> : <Eye size={16} color={colors.white} />}</Pressable>
      </View>
      <Text style={{ color: colors.white, fontSize: 30, fontWeight: "700" }}>{hidden ? copy.hiddenBalance : vnd(account.data?.balance ?? 0)}</Text>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
          <ShieldCheck size={14} color={colors.aqua} />
          <Text style={{ color: colors.aqua, fontSize: type.small }}>{copy.protected}</Text>
        </View>
        <Pressable testID={selected ? "deposits-link" : undefined} accessibilityRole="button" onPress={() => router.push("/(main)/savings")}>
          <Text style={{ color: colors.white, fontWeight: "700", fontSize: type.small }}>{copy.deposits}</Text>
        </Pressable>
      </View>
    </Pressable>
  )
}

function RecentList({ accountId }: { accountId: string | null }) {
  const router = useRouter()
  const account = useAccount(accountId)
  const history = useHistory(accountId)
  const type = useType()
  const recent = statement(history.data?.entries ?? [], account.data?.balance ?? 0).slice(0, 5)
  return (
    <View style={{ gap: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.recent}</Text>
        <Pressable accessibilityRole="button" onPress={() => router.push("/(main)/history")}>
          <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>{copy.seeAll}</Text>
        </Pressable>
      </View>
      {recent.map((item) => {
        const stamp = when(item.ts)
        const inbound = item.direction === "in"
        const title = movementTitle({ ...item, counterparty_name: displayHolder(item.counterparty_name) })
        return (
          <View key={`${item.tx_id}-${item.direction}`} testID="recent-tx" style={{ flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12, gap: 12 }}>
            <Text style={{ color: colors.navy, fontWeight: "600", fontSize: type.body, flex: 1 }}>{title}</Text>
            <View>
              <Text style={{ color: inbound ? "#2E7D32" : colors.navy, fontWeight: "700", textAlign: "right" }}>{inbound ? "+" : "−"}{vnd(item.amount)}</Text>
              <Text style={{ color: "#94A3B8", fontSize: type.small, textAlign: "right" }}>{stamp.time}</Text>
              <Text style={{ color: "#94A3B8", fontSize: type.small, textAlign: "right" }}>{stamp.date}</Text>
            </View>
          </View>
        )
      })}
    </View>
  )
}

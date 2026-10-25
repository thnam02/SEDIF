import { useState } from "react"
import { Pressable, Text, View } from "react-native"
import { useLocalSearchParams, useRouter } from "expo-router"
import { FidesGuardianRequest } from "fides-sdk"

import { useConfirmGuardian, useGuardianRequests, useHistory, useRespond } from "../api/queries"
import { copy } from "../copy/vi"
import { vnd, weekdayIndex } from "../format"
import { personas } from "../personas"
import { useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

export function StatsScreen() {
  const accountId = useBank((state) => state.accountId)
  const history = useHistory(accountId)
  const bins = Array.from({ length: 7 }, () => ({ in: 0, out: 0 }))
  for (const item of history.data?.entries ?? []) {
    const bin = bins[weekdayIndex(item.ts)]
    if (!bin) continue
    if (item.direction === "in") bin.in += item.amount
    else bin.out += item.amount
  }
  const peak = Math.max(1, ...bins.flatMap((bin) => [bin.in, bin.out]))
  const incoming = bins.reduce((sum, bin) => sum + bin.in, 0)
  const outgoing = bins.reduce((sum, bin) => sum + bin.out, 0)
  return (
    <Screen>
      <Header title={copy.stats} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.incoming}</Text>
        <Text style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }}>{vnd(incoming)}</Text>
        <Text style={{ color: "#64748B" }}>{copy.outgoing} {vnd(outgoing)}</Text>
      </Card>
      <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 6, height: 120 }}>
        {bins.map((bin, index) => (
          <View key={copy.week[index]} style={{ flex: 1, alignItems: "center", gap: 4 }}>
            <View style={{ width: "70%", height: Math.max(4, (bin.out / peak) * 80), backgroundColor: colors.deep, borderRadius: 4 }} />
            <Text style={{ color: colors.navy, fontSize: 10 }}>{copy.week[index]}</Text>
          </View>
        ))}
      </View>
    </Screen>
  )
}

export function HistoryScreen() {
  const accountId = useBank((state) => state.accountId)
  const history = useHistory(accountId)
  const [filter, setFilter] = useState<"all" | "in" | "out" | "held">("all")
  const rows = (history.data?.entries ?? []).filter((item) => {
    if (filter === "in") return item.direction === "in"
    if (filter === "out") return item.direction === "out"
    if (filter === "held") return item.status !== "done"
    return true
  })
  const type = useType()
  return (
    <Screen>
      <Header title={copy.history} />
      <View style={{ flexDirection: "row", gap: 8 }}>
        {([["all", copy.all], ["in", copy.incoming], ["out", copy.outgoing], ["held", copy.held]] as const).map(([key, label]) => (
          <Pressable key={key} onPress={() => setFilter(key)} style={{ borderRadius: 12, backgroundColor: filter === key ? colors.deep : colors.mint, paddingHorizontal: 10, paddingVertical: 8 }}>
            <Text style={{ color: filter === key ? colors.white : colors.deep, fontSize: type.small }}>{label}</Text>
          </Pressable>
        ))}
      </View>
      {rows.map((item) => (
        <View key={item.tx_id} style={{ flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12 }}>
          <Text style={{ color: colors.navy }}>{item.purpose_text || item.tx_type}</Text>
          <Text style={{ color: item.status !== "done" ? colors.warn : colors.deep }}>{item.status !== "done" ? copy.held : vnd(item.amount)}</Text>
        </View>
      ))}
    </Screen>
  )
}

export function ProfileScreen() {
  const router = useRouter()
  const role = useBank((state) => state.role)
  const accountId = useBank((state) => state.accountId)
  const large = useBank((state) => state.largeText)
  const setLarge = useBank((state) => state.setLargeText)
  const desk = useBank((state) => state.guardianDesk)
  const setDesk = useBank((state) => state.setGuardianDesk)
  const requests = useGuardianRequests()
  const person = personas.find((item) => item.role === role)
  const label = copy.personas[role as keyof typeof copy.personas]
  const type = useType()
  const pending = requests.data?.requests ?? []
  return (
    <Screen>
      <Header title={desk ? copy.pending : copy.profile} />
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
          <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{desk ? "NT" : label?.mark}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{desk ? copy.guardianName : person?.full_name}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>{desk ? copy.loved : label?.title}</Text>
            {desk ? null : <Text style={{ color: "#64748B", fontSize: type.small }}>{accountId}</Text>}
          </View>
        </View>
      </Card>
      <Pressable accessibilityRole="switch" accessibilityState={{ checked: large }} onPress={() => setLarge(!large)} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderRadius: 16, backgroundColor: colors.mint, padding: 16 }}>
        <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{copy.largeText}</Text>
        <View style={{ width: 44, height: 26, borderRadius: 13, backgroundColor: large ? colors.deep : "#CBD5E1", justifyContent: "center", paddingHorizontal: 3 }}>
          <View style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: colors.white, alignSelf: large ? "flex-end" : "flex-start" }} />
        </View>
      </Pressable>
      {person?.has_guardian ? (
        <Button label={desk ? copy.profile : copy.viewAsGuardian} outline onPress={() => setDesk(!desk)} />
      ) : null}
      {desk ? (
        pending.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptyPending}</Text> : pending.map((item) => (
          <Button key={item.id} outline label={`${item.sender_name ?? item.account_id}${item.amount ? ` · ${vnd(item.amount)}` : ""}`} onPress={() => router.push(`/(main)/guardian/requests/${item.id}`)} />
        ))
      ) : (
        <>
          <Button label={copy.loved} onPress={() => router.push("/(main)/guardian")} />
          <Button label={copy.limits} onPress={() => router.push("/(main)/limit")} />
          <Button outline label={copy.signOut} onPress={() => router.replace("/(auth)/persona")} />
        </>
      )}
    </Screen>
  )
}

export function GuardianScreen() {
  const router = useRouter()
  const role = useBank((state) => state.role)
  const requests = useGuardianRequests()
  const person = personas.find((item) => item.role === role)
  const type = useType()
  return (
    <Screen>
      <Header title={copy.loved} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.lovedBody}</Text>
        <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{person?.guardian?.relation ?? person?.full_name}</Text>
        <Text style={{ color: "#64748B" }}>{copy.wait24}</Text>
      </Card>
      <Button label={copy.addLoved} />
      {(requests.data?.requests ?? []).map((item) => (
        <Button key={item.id} outline label={item.id} onPress={() => router.push(`/(main)/guardian/requests/${item.id}`)} />
      ))}
    </Screen>
  )
}

export function GuardianRequestScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const requests = useGuardianRequests()
  const confirm = useConfirmGuardian()
  const respond = useRespond()
  const item = (requests.data?.requests ?? []).find((row) => row.id === id)
  const largeText = useBank((state) => state.largeText)
  if (!item) return <Screen><Header title={copy.loved} /></Screen>
  return (
    <Screen>
      <FidesGuardianRequest
        largeText={largeText}
        request={{
          decision_id: item.transfer_id ? `dec-${item.transfer_id}` : item.id,
          sender_name: item.sender_name ?? undefined,
          payee_name: item.to_account ?? undefined,
          amount_text: item.amount == null ? undefined : vnd(item.amount),
        }}
        onDecide={(body) => {
          if (body.guardian_confirm) confirm.mutate(item.id)
          else if (body.cancel && item.transfer_id) respond.mutate({ id: item.transfer_id, body: { cancel: true } })
        }}
      />
    </Screen>
  )
}

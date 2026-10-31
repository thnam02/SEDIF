import { useState } from "react"
import { Pressable, Text, TextInput, View } from "react-native"
import { useLocalSearchParams, useRouter } from "expo-router"
import { FidesGuardianRequest } from "fides-sdk"

import { useAccount, useChoice, useConfirmGuardian, useGuardianRequests, useHistory, useRegisterGuardian } from "../api/queries"
import { copy } from "../copy/vi"
import { displayHolder, payeeName } from "../names"
import { fullDate, movementTitle, presentHistory, showAccount, statement, vnd, when } from "../format"
import { axisVnd, buildStats, compactVnd, type StatsPeriodId } from "../stats"
import { personas } from "../personas"
import { useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

const IN_COLOR = "#2E7D32"
const PLOT_HEIGHT = 128

export function StatsScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const history = useHistory(accountId)
  const type = useType()
  const [period, setPeriod] = useState<StatsPeriodId>("6m")
  const [selected, setSelected] = useState<number | null>(null)
  const [plotWidth, setPlotWidth] = useState(0)
  const entries = presentHistory(history.data?.entries ?? [])
  const report = buildStats(entries, period)
  const periodLabel = copy.statsPeriods.find(([id]) => id === period)?.[1] ?? ""
  const net = report.incoming - report.outgoing
  const peak = Math.max(1, ...report.buckets.map((bucket) => Math.max(bucket.incoming, bucket.outgoing)))
  const slot = plotWidth / Math.max(report.buckets.length, 1)
  const barWidth = slot > 0 ? Math.min(16, Math.max(8, (slot - 4) / 2)) : 8
  const labelWidth = Math.max(...report.buckets.map((bucket) => bucket.label.length), 1) * 7
  const skipLabels = labelWidth > slot && slot > 0
  const chosen = selected == null ? null : report.buckets[selected]
  return (
    <Screen>
      <Header title={copy.stats} />
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        {copy.statsPeriods.map(([id, label]) => {
          const active = id === period
          return (
            <Pressable key={id} testID={`stats-period-${id}`} accessibilityRole="button" onPress={() => { setPeriod(id); setSelected(null) }} style={{ borderRadius: 12, backgroundColor: active ? colors.deep : colors.mint, paddingHorizontal: 12, paddingVertical: 8 }}>
              <Text style={{ color: active ? colors.white : colors.deep, fontSize: type.small, fontWeight: "700" }}>{label}</Text>
            </Pressable>
          )
        })}
      </View>
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{periodLabel} · {copy.txCount.replace("{n}", String(report.count))}</Text>
        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
          <Text style={{ color: "#64748B" }}>{copy.incoming}</Text>
          <Text style={{ color: IN_COLOR, fontWeight: "700" }}>{compactVnd(report.incoming)}</Text>
        </View>
        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
          <Text style={{ color: "#64748B" }}>{copy.outgoing}</Text>
          <Text style={{ color: colors.deep, fontWeight: "700" }}>{compactVnd(report.outgoing)}</Text>
        </View>
        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
          <Text style={{ color: "#64748B" }}>{copy.net}</Text>
          <Text style={{ color: net < 0 ? colors.danger : IN_COLOR, fontWeight: "700" }}>{compactVnd(net)}</Text>
        </View>
      </Card>
      {report.count === 0 ? <Text style={{ color: "#64748B" }}>{copy.statsEmpty}</Text> : (
        <View testID="stats-chart" style={{ gap: 12 }}>
          <View style={{ flexDirection: "row", gap: 8 }}>
            <View style={{ width: 52, height: PLOT_HEIGHT, justifyContent: "space-between" }}>
              {[peak, peak / 2, 0].map((value, index) => (
                <Text key={index} numberOfLines={1} style={{ color: "#94A3B8", fontSize: 10 }}>{axisVnd(value, peak)}</Text>
              ))}
            </View>
            <View style={{ flex: 1 }} onLayout={(event) => setPlotWidth(event.nativeEvent.layout.width)}>
              <View style={{ height: PLOT_HEIGHT, justifyContent: "space-between" }}>
                {[0, 1, 2].map((line) => <View key={line} style={{ borderTopWidth: 1, borderColor: "#E6EEF5" }} />)}
              </View>
              <View style={{ position: "absolute", left: 0, right: 0, top: 0, height: PLOT_HEIGHT, flexDirection: "row", alignItems: "flex-end" }}>
                {report.buckets.map((bucket, index) => (
                  <Pressable key={bucket.startMs} testID={`stats-bucket-${index}`} accessibilityRole="button" onPress={() => setSelected(index)} style={{ flex: 1, height: "100%", alignItems: "center", justifyContent: "flex-end", borderRadius: 8, backgroundColor: selected === index ? colors.mint : "transparent" }}>
                    <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 2 }}>
                      <View style={{ width: barWidth, height: Math.max(bucket.incoming > 0 ? 2 : 0, (bucket.incoming / peak) * (PLOT_HEIGHT - 8)), backgroundColor: IN_COLOR, borderRadius: 3 }} />
                      <View style={{ width: barWidth, height: Math.max(bucket.outgoing > 0 ? 2 : 0, (bucket.outgoing / peak) * (PLOT_HEIGHT - 8)), backgroundColor: colors.deep, borderRadius: 3 }} />
                    </View>
                  </Pressable>
                ))}
              </View>
            </View>
          </View>
          <View style={{ flexDirection: "row", marginLeft: 60, overflow: "visible" }}>
            {report.buckets.map((bucket, index) => {
              const hidden = skipLabels && index % 2 === 1
              const width = Math.max(slot, bucket.label.length * 7)
              return (
                <View key={bucket.startMs} style={{ flex: 1, overflow: "visible", alignItems: index === report.buckets.length - 1 ? "flex-end" : "center" }}>
                  {hidden ? null : <Text numberOfLines={1} style={{ width, textAlign: "center", color: "#64748B", fontSize: 10 }}>{bucket.label}</Text>}
                </View>
              )
            })}
          </View>
          {chosen ? <Text style={{ color: colors.navy, fontSize: type.small }}>{chosen.label}: vào {compactVnd(chosen.incoming)} · ra {compactVnd(chosen.outgoing)} · {copy.txCount.replace("{n}", String(chosen.count))}</Text> : null}
          <View style={{ flexDirection: "row", gap: 16 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: IN_COLOR }} />
              <Text style={{ color: colors.navy, fontSize: type.small }}>{copy.incoming}</Text>
            </View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.deep }} />
              <Text style={{ color: colors.navy, fontSize: type.small }}>{copy.outgoing}</Text>
            </View>
          </View>
        </View>
      )}
      <Button label={copy.lookup} onPress={() => router.push("/(main)/history")} />
    </Screen>
  )
}

export function HistoryScreen() {
  const accountId = useBank((state) => state.accountId)
  const account = useAccount(accountId)
  const history = useHistory(accountId)
  const [filter, setFilter] = useState<"all" | "in" | "out" | "held">("all")
  const [query, setQuery] = useState("")
  const type = useType()
  const needle = query.trim().toLowerCase()
  const rows = statement(presentHistory(history.data?.entries ?? []), account.data?.balance ?? 0).filter((item) => {
    if (filter === "in" && item.direction !== "in") return false
    if (filter === "out" && item.direction !== "out") return false
    if (filter === "held" && item.status === "done") return false
    if (!needle) return true
    const label = movementTitle({ ...item, counterparty_name: payeeName(item.counterparty_name, item.counterparty_id) })
    return `${item.purpose_text ?? ""} ${label} ${item.amount}`.toLowerCase().includes(needle)
  })
  let day = ""
  return (
    <Screen>
      <Header title={copy.lookup} />
      <Text style={{ color: "#64748B" }}>{copy.movement}</Text>
      <TextInput
        value={query}
        onChangeText={setQuery}
        placeholder={copy.searchTx}
        placeholderTextColor="#94A3B8"
        style={{ borderRadius: 12, backgroundColor: colors.mint, paddingHorizontal: 12, paddingVertical: 10, color: colors.navy, fontSize: type.body }}
      />
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        {([["all", copy.all], ["in", copy.incoming], ["out", copy.outgoing], ["held", copy.held]] as const).map(([key, label]) => (
          <Pressable key={key} onPress={() => setFilter(key)} style={{ borderRadius: 12, backgroundColor: filter === key ? colors.deep : colors.mint, paddingHorizontal: 10, paddingVertical: 8 }}>
            <Text style={{ color: filter === key ? colors.white : colors.deep, fontSize: type.small }}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.txCount.replace("{n}", String(rows.length))}</Text>
      {rows.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptyTx}</Text> : null}
      {rows.map((item) => {
        const stamp = when(item.ts)
        const showDay = fullDate(item.ts) !== day
        day = fullDate(item.ts)
        const inbound = item.direction === "in"
        const who = payeeName(item.counterparty_name, item.counterparty_id)
        const title = movementTitle({ ...item, counterparty_name: who })
        const party = item.counterparty_id ? `${copy.bankName} · ${showAccount(item.counterparty_id)}` : ""
        return (
          <View key={`${item.tx_id}-${item.direction}`} style={{ gap: 8 }}>
            {showDay ? <Text style={{ color: colors.deep, fontWeight: "700" }}>{day}</Text> : null}
            <View style={{ flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12, gap: 12 }}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: colors.navy, fontWeight: "600", fontSize: type.body }}>{title}</Text>
                {party ? <Text style={{ color: "#64748B", fontSize: type.small }}>{party}</Text> : null}
                <Text style={{ color: "#64748B", fontSize: type.small }}>{stamp.time} · {stamp.date}</Text>
              </View>
              <View>
                <Text style={{ color: item.status !== "done" ? colors.warn : inbound ? "#2E7D32" : colors.navy, fontWeight: "700", textAlign: "right" }}>{item.status !== "done" ? copy.held : `${inbound ? "+" : "−"}${vnd(item.amount)}`}</Text>
                <Text style={{ color: "#64748B", fontSize: type.small, textAlign: "right" }}>{copy.balanceAfter} {vnd(item.balanceAfter)}</Text>
              </View>
            </View>
          </View>
        )
      })}
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
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{desk ? "GH" : label?.mark}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{desk ? copy.guardianName : person?.full_name}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>{desk ? `${(person?.guardian?.relation ?? "người giám hộ").replaceAll("_", " ")} · ${person?.full_name ?? ""}` : label?.hint}</Text>
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
          <Button key={item.id} outline label={`${displayHolder(item.sender_name) || item.account_id}${item.amount ? ` · ${vnd(item.amount)}` : ""}`} onPress={() => router.push(`/(main)/guardian/requests/${item.id}`)} />
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
  const accountId = useBank((state) => state.accountId) ?? ""
  const requests = useGuardianRequests()
  const register = useRegisterGuardian()
  const type = useType()
  const [phone, setPhone] = useState("")
  const [relation, setRelation] = useState("")
  const [consent, setConsent] = useState(false)
  const [saved, setSaved] = useState(false)
  return (
    <Screen>
      <Header title={copy.loved} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.lovedBody}</Text>
      </Card>
      <TextInput testID="guardian-phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" placeholder={copy.guardianPhone} style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12, color: colors.navy, fontSize: type.body }} />
      <TextInput testID="guardian-relation" value={relation} onChangeText={setRelation} placeholder={copy.guardianRelation} style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12, color: colors.navy, fontSize: type.body }} />
      <Pressable testID="guardian-consent" accessibilityRole="checkbox" accessibilityState={{ checked: consent }} onPress={() => setConsent(!consent)} style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <View style={{ width: 22, height: 22, borderRadius: 4, borderWidth: 1, borderColor: colors.deep, backgroundColor: consent ? colors.deep : colors.white }} />
        <Text style={{ flex: 1, color: colors.navy, fontSize: type.body }}>{copy.guardianConsent}</Text>
      </Pressable>
      <Button
        testID="guardian-save"
        label={copy.addLoved}
        onPress={() => {
          if (!consent || !phone.trim() || !relation.trim()) return
          register.mutate(
            { account_id: accountId, phone: phone.trim(), relation: relation.trim(), consent: true },
            { onSuccess: () => setSaved(true) },
          )
        }}
      />
      {saved ? <Text style={{ color: "#2E7D32", fontWeight: "700" }}>{copy.guardianSaved}</Text> : null}
      {register.isError ? <Text style={{ color: colors.danger }}>{register.error.message}</Text> : null}
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
  const choice = useChoice()
  const item = (requests.data?.requests ?? []).find((row) => row.id === id)
  const largeText = useBank((state) => state.largeText)
  const [released, setReleased] = useState(false)
  if (!item) {
    return (
      <Screen>
        <Header title={copy.loved} />
        {released ? <Text style={{ color: "#2E7D32", fontSize: 22, fontWeight: "700", textAlign: "center" }}>{copy.released}</Text> : null}
      </Screen>
    )
  }
  return (
    <Screen>
      <FidesGuardianRequest
        largeText={largeText}
        request={{
          decision_id: item.transfer_id ? `dec-${item.transfer_id}` : item.id,
          sender_name: displayHolder(item.sender_name) || undefined,
          payee_name: item.to_account ?? undefined,
          amount_text: item.amount == null ? undefined : vnd(item.amount),
        }}
        onDecide={(body) => {
          if (body.guardian_confirm) confirm.mutate(item.id, { onSuccess: () => setReleased(true) })
          else if (body.cancel && item.transfer_id) choice.mutate({ decisionId: `dec-${item.transfer_id}`, choice: "cancel", responseMs: 0 })
        }}
      />
      {released ? <Text style={{ color: "#2E7D32", fontSize: 22, fontWeight: "700", textAlign: "center" }}>{copy.released}</Text> : null}
    </Screen>
  )
}

import { useEffect, useState } from "react"
import { Pressable, Text, TextInput, View } from "react-native"
import { useRouter } from "expo-router"
import { Check, ChevronRight, Search, ShieldCheck } from "lucide-react-native"
import { FidesIntervention, FidesSession, useAccountInput } from "fides-sdk"

import { useBeneficiaries, useRespond, useTransfer } from "../api/queries"
import { copy } from "../copy/vi"
import { vnd } from "../format"
import { useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

export function RecipientScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const setPayee = useBank((state) => state.setPayee)
  const saved = useBeneficiaries(accountId)
  const input = useAccountInput()
  const [query, setQuery] = useState("")
  const type = useType()
  useEffect(() => { FidesSession.start() }, [])
  const rows = (saved.data?.beneficiaries ?? []).filter((item) => `${item.holder_name} ${item.account_id}`.toLowerCase().includes(query.trim().toLowerCase()))
  function go(account: string, name: string, picked: boolean) {
    if (picked) input.choose(account)
    setPayee(account, name)
    router.push("/(main)/transfer/amount")
  }
  return (
    <Screen>
      <Header title={copy.recipient} />
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: "#F8FAFC", paddingHorizontal: 12, paddingVertical: 12 }}>
        <Search size={17} color="#94A3B8" />
        <TextInput value={query} onChangeText={setQuery} placeholder={copy.search} style={{ flex: 1, color: colors.navy, fontSize: type.body }} />
      </View>
      {rows.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptyPayee}</Text> : null}
      {rows.map((item) => (
        <Pressable key={item.id} accessibilityRole="button" onPress={() => go(item.account_id, item.holder_name, true)} style={{ flexDirection: "row", alignItems: "center", gap: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12 }}>
          <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{item.holder_name.slice(0, 1)}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{item.holder_name}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>Sao Bank · {item.account_id}</Text>
          </View>
          <ChevronRight size={18} color={colors.deep} />
        </Pressable>
      ))}
      <TextInput value={input.value} onChangeText={input.onChangeText} placeholder={copy.newRecipient} autoCapitalize="characters" style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12, color: colors.navy }} />
      <Button outline label={copy.newRecipient} onPress={() => input.value && go(input.value, input.value, false)} />
    </Screen>
  )
}

export function AmountScreen() {
  const router = useRouter()
  const amount = useBank((state) => state.amount)
  const setAmount = useBank((state) => state.setAmount)
  const note = useBank((state) => state.note)
  const setNote = useBank((state) => state.setNote)
  const name = useBank((state) => state.toName)
  const type = useType()
  return (
    <Screen>
      <Header title={copy.amount} />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{name} · Sao Bank</Text>
        <TextInput testID="amount" value={String(amount)} onChangeText={(value) => setAmount(Number(value.replace(/\D/g, "")) || 0)} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.dong}</Text>
      </Card>
      <View style={{ flexDirection: "row", gap: 8 }}>
        {copy.chips.map((chip) => (
          <Pressable key={chip} onPress={() => setAmount(Number(chip))} style={{ borderRadius: 8, backgroundColor: colors.mint, paddingHorizontal: 8, paddingVertical: 8 }}>
            <Text style={{ color: colors.deep, fontSize: type.small }}>{Number(chip).toLocaleString("vi-VN")}</Text>
          </Pressable>
        ))}
      </View>
      <TextInput value={note} onChangeText={setNote} placeholder={copy.note} style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12 }} />
      <Button label={copy.confirm} onPress={() => router.push("/(main)/transfer/confirm")} />
    </Screen>
  )
}

export function ConfirmScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId) ?? ""
  const toAccount = useBank((state) => state.toAccount)
  const toName = useBank((state) => state.toName)
  const amount = useBank((state) => state.amount)
  const note = useBank((state) => state.note)
  const setOutcome = useBank((state) => state.setOutcome)
  const transfer = useTransfer()
  const type = useType()
  return (
    <Screen>
      <Header title={copy.confirm} />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.payee}</Text>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{toName}</Text>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.moneyLabel}</Text>
        <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{vnd(amount)}</Text>
      </Card>
      {amount >= 10_000_000 ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <ShieldCheck size={16} color={colors.warn} />
          <Text style={{ color: colors.warn, fontSize: type.small }}>{copy.face}</Text>
        </View>
      ) : null}
      <Button
        label={copy.confirm}
        onPress={() => {
          FidesSession.collectSignals()
          transfer.mutate(
            { from: accountId, to: toAccount, amount, note },
            {
              onSuccess: (result) => {
                setOutcome({
                  transferId: result.transfer_id,
                  status: result.status,
                  level: result.decision.level,
                  actions: result.decision.actions,
                  reasons: result.decision.reasons,
                  funds_status: result.funds_status,
                  gate: "none",
                  hold_until: result.hold_until,
                  countdown_until: null,
                  payee: toAccount,
                  payeeName: toName,
                  amount,
                })
                router.replace("/(main)/transfer/result")
              },
            },
          )
        }}
      />
      {transfer.isError ? <Text style={{ color: colors.danger }}>{transfer.error.message}</Text> : null}
    </Screen>
  )
}

export function ResultScreen() {
  const router = useRouter()
  const outcome = useBank((state) => state.outcome)
  const setOutcome = useBank((state) => state.setOutcome)
  const respond = useRespond()
  const type = useType()
  const largeText = useBank((state) => state.largeText)
  if (!outcome) return <Screen><Header title={copy.success} /></Screen>
  if (outcome.status !== "done") {
    return (
      <Screen>
        <FidesIntervention
          largeText={largeText}
          decision={{
            decision_id: outcome.transferId,
            level: outcome.level,
            actions: outcome.actions,
            reasons: outcome.reasons,
            hold_until: outcome.hold_until,
            countdown_until: outcome.countdown_until,
            funds_status: outcome.funds_status,
            gate: outcome.gate,
            payee_name: outcome.payeeName,
            amount_text: vnd(outcome.amount),
            channel: "Chuyển khoản",
          }}
          onRespond={(body) => {
            respond.mutate(
              { id: outcome.transferId, body: { who_guides: body.who_guides, guardian_confirm: body.guardian_confirm, hung_up: body.hung_up, proceed: body.proceed, cancel: body.cancel } },
              {
                onSuccess: (next) => setOutcome({
                  ...outcome,
                  status: next.status,
                  level: next.level,
                  funds_status: next.funds_status,
                  gate: next.gate,
                  hold_until: next.hold_until,
                  countdown_until: next.countdown_until,
                }),
              },
            )
          }}
        />
      </Screen>
    )
  }
  return (
    <Screen>
      <View style={{ alignItems: "center", gap: 20, paddingVertical: 40 }}>
        <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
          <Check size={36} color={colors.deep} />
        </View>
        <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.success}</Text>
        <Text style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }}>{vnd(outcome.amount)}</Text>
        <Text style={{ color: "#64748B", fontSize: type.body }}>{outcome.payeeName} · {copy.code} {outcome.transferId}</Text>
        <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
      </View>
    </Screen>
  )
}

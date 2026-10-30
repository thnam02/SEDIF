import { useEffect, useRef, useState } from "react"
import { Pressable, Text, TextInput, View } from "react-native"
import { useRouter } from "expo-router"
import { Check, ChevronRight, Search, ShieldCheck } from "lucide-react-native"
import { FidesCallNotice, FidesSession, sessionRecorder, useAccountInput } from "fides-sdk"

import { BANK_URL } from "../api/client"
import { useBeneficiaries, useChoice, usePurposePolicy, useTransfer } from "../api/queries"
import { rememberSession } from "../dev/lastSession"
import { uploadExperiment } from "../dev/upload"
import { copy } from "../copy/vi"
import { displayHolder } from "../names"
import { digits, grouped, vnd } from "../format"
import { DEMO, useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

export function RecipientScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const setPayee = useBank((state) => state.setPayee)
  const saved = useBeneficiaries(accountId)
  const input = useAccountInput()
  const [query, setQuery] = useState("")
  const type = useType()
  useEffect(() => {
    if (!sessionRecorder.isOpen()) {
      FidesSession.start()
      sessionRecorder.start("new_typed")
    }
    sessionRecorder.note({ type: "screen_enter", screen: "recipient" })
  }, [])
  const rows = (saved.data?.beneficiaries ?? []).filter((item) => `${item.holder_name} ${item.account_id}`.toLowerCase().includes(query.trim().toLowerCase()))
  function go(account: string, name: string) {
    input.choose(account)
    setPayee(account, name)
    router.push("/(main)/transfer/amount")
  }
  return (
    <Screen call>
      <Header title={copy.recipient} />
      <Button testID="new-account-open" outline label={copy.newRecipient} onPress={() => router.push("/(main)/transfer/new")} />
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: "#F8FAFC", paddingHorizontal: 12, paddingVertical: 12 }}>
        <Search size={17} color="#94A3B8" />
        <TextInput value={query} onChangeText={setQuery} placeholder={copy.search} style={{ flex: 1, color: colors.navy, fontSize: type.body }} />
      </View>
      {rows.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptyPayee}</Text> : null}
      {rows.map((item) => (
        <Pressable key={item.id} accessibilityRole="button" onPress={() => go(item.account_id, displayHolder(item.holder_name))} style={{ flexDirection: "row", alignItems: "center", gap: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12 }}>
          <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{displayHolder(item.holder_name).slice(0, 1)}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{displayHolder(item.holder_name)}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>BVC bank · {item.account_id}</Text>
          </View>
          <ChevronRight size={18} color={colors.deep} />
        </Pressable>
      ))}
    </Screen>
  )
}

export function NewAccountScreen() {
  const router = useRouter()
  const setPayee = useBank((state) => state.setPayee)
  const input = useAccountInput()
  const field = useRef<TextInput>(null)
  const type = useType()
  useEffect(() => { sessionRecorder.note({ type: "screen_enter", screen: "new_account" }) }, [])
  function submit() {
    const account = input.value.trim()
    if (!account) {
      field.current?.focus()
      return
    }
    setPayee(account, account)
    router.push("/(main)/transfer/amount")
  }
  return (
    <Screen call>
      <Header title={copy.newRecipient} />
      <TextInput ref={field} testID="new-account" value={input.value} onChangeText={input.onChangeText} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "account" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "account" })} onSubmitEditing={submit} placeholder={copy.newRecipient} autoCapitalize="characters" autoFocus returnKeyType="go" style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12, color: colors.navy, fontSize: type.body }} />
      {DEMO ? (
        <Pressable testID="paste-account" accessibilityRole="button" onPress={() => input.onChangeText("ZVAY000000000001")} style={{ minHeight: 44, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.deep, fontWeight: "700" }}>Dán số tài khoản</Text>
        </Pressable>
      ) : null}
      <Button testID="new-account-go" label={copy.continueTransfer} onPress={submit} />
    </Screen>
  )
}

function AmountNotice() {
  const accountId = useBank((state) => state.accountId)
  const [inCall, setInCall] = useState(false)
  const [used, setUsed] = useState(0)
  const [max, setMax] = useState(3)
  const asked = useRef(false)
  useEffect(() => {
    const read = () => setInCall(FidesSession.callState().in_call === true)
    read()
    const stop = FidesSession.onCallStateChanged(read)
    const timer = setInterval(read, 1000)
    return () => {
      stop()
      clearInterval(timer)
    }
  }, [])
  useEffect(() => {
    if (!inCall || !accountId || asked.current) return
    asked.current = true
    fetch(`${BANK_URL}/accounts/${accountId}/call-notice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ demo: DEMO }),
    })
      .then((response) => response.json())
      .then((body: { used?: number; max?: number }) => {
        if (typeof body.used === "number") setUsed(body.used)
        if (typeof body.max === "number") setMax(body.max)
      })
      .catch(() => undefined)
  }, [accountId, inCall])
  return <FidesCallNotice inCall={inCall} used={used} max={max} demo={DEMO} />
}

export function AmountScreen() {
  const router = useRouter()
  const amount = useBank((state) => state.amount)
  const setAmount = useBank((state) => state.setAmount)
  const note = useBank((state) => state.note)
  const setNote = useBank((state) => state.setNote)
  const name = useBank((state) => state.toName)
  const type = useType()
  useEffect(() => { sessionRecorder.note({ type: "screen_enter", screen: "amount" }) }, [])
  function onAmount(value: string) {
    const next = digits(value)
    const prev = digits(String(amount))
    if (next.length < prev.length) sessionRecorder.note({ type: "field_edit" })
    else if (next.length - prev.length > 1) sessionRecorder.note({ type: "paste" })
    else if (next.length > prev.length) sessionRecorder.note({ type: "key_tick" })
    setAmount(Number(next) || 0)
  }
  function onNote(value: string) {
    if (value.length < note.length) sessionRecorder.note({ type: "field_edit" })
    else if (value.length - note.length > 1) sessionRecorder.note({ type: "paste" })
    else if (value.length > note.length) sessionRecorder.note({ type: "key_tick" })
    setNote(value)
  }
  return (
    <Screen call>
      <Header title={copy.amount} />
      <AmountNotice />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{name} · BVC bank</Text>
        <TextInput testID="amount" value={grouped(amount)} onChangeText={onAmount} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "amount" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "amount" })} selectTextOnFocus keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.dong}</Text>
      </Card>
      <View style={{ flexDirection: "row", gap: 8 }}>
        {copy.chips.map((chip) => (
          <Pressable key={chip} onPress={() => setAmount(Number(chip))} style={{ borderRadius: 8, backgroundColor: colors.mint, paddingHorizontal: 8, paddingVertical: 8 }}>
            <Text style={{ color: colors.deep, fontSize: type.small }}>{grouped(Number(chip))}</Text>
          </Pressable>
        ))}
      </View>
      <TextInput value={note} onChangeText={onNote} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "note" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "note" })} placeholder={copy.note} style={{ borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16, paddingVertical: 12 }} />
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
  const saved = useBeneficiaries(accountId)
  const policy = usePurposePolicy()
  const type = useType()
  const [purpose, setPurpose] = useState<(typeof copy.purposes)[number][0] | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  useEffect(() => { sessionRecorder.note({ type: "screen_enter", screen: "confirm" }) }, [])
  const known = (saved.data?.beneficiaries ?? []).some((item) => item.account_id === toAccount)
  const threshold = policy.data?.purpose_amount_vnd ?? 10_000_000
  const askPurpose = saved.isSuccess && (!known || amount > threshold)
  const waiting = !saved.isSuccess || policy.isPending
  return (
    <Screen call>
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
      {askPurpose && !banner ? (
        <View style={{ gap: 8 }}>
          <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.purposeQuestion}</Text>
          {copy.purposes.map(([id, label]) => (
            <Pressable key={id} testID={`purpose-${id}`} accessibilityRole="button" onPress={() => setPurpose(id)} style={{ minHeight: 44, justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: purpose === id ? colors.deep : "#E2E8F0", backgroundColor: purpose === id ? colors.mint : colors.white, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: colors.navy, fontSize: type.body }}>{label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      {banner ? <Text testID="level-1-line" style={{ color: colors.navy, fontSize: type.body }}>{banner}</Text> : null}
      {banner ? <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} /> : (
      <Button
        testID="confirm-transfer"
        label={copy.confirm}
        onPress={() => {
          if (waiting || (askPurpose && !purpose) || transfer.isPending) return
          const call = FidesSession.callBlock()
          const session = sessionRecorder.end()
          const device = { ...FidesSession.collectSignals(), state: "unknown" as const }
          rememberSession({ task_type: session.task_type, call, behaviour_features: session.behaviour_features })
          const experiment = session.experiment
          if (experiment && session.raw_events) {
            void uploadExperiment({
              participant_id: experiment.participant_id,
              condition: experiment.condition,
              session_index: experiment.session_index,
              task_type: session.task_type,
              call,
              behaviour_features: session.behaviour_features,
              raw_events: session.raw_events,
            }).catch(() => undefined)
          }
          transfer.mutate(
            {
              from: accountId,
              to: toAccount,
              amount,
              note,
              task_type: session.task_type,
              call,
              behaviour_features: session.behaviour_features,
              transfer_purpose: askPurpose && purpose ? purpose : "not_asked",
              device,
              experiment: session.experiment,
            },
            {
              onSuccess: (result) => {
                if (experiment) {
                  console.log(JSON.stringify({
                    experiment,
                    level: result.decision.level,
                    engine_level: result.decision.engine_level ?? null,
                    rules_fired: result.decision.rules_fired ?? [],
                    evidence_card: result.decision.evidence_card ?? null,
                  }))
                }
                const engineLevel = result.decision.engine_level ?? 0
                const reasonsVi = result.decision.evidence_card?.reasons_vi ?? []
                setOutcome({
                  transferId: result.transfer_id,
                  decisionId: `dec-${result.transfer_id}`,
                  status: experiment ? "done" : result.status,
                  level: result.decision.level,
                  engineLevel,
                  intervention: result.decision.intervention ?? (engineLevel === 3 ? "security_workflow" : engineLevel === 2 ? "three_button" : engineLevel === 1 ? "banner" : "none"),
                  actions: result.decision.actions,
                  reasons: result.decision.reasons,
                  reasonsVi,
                  familiesHit: result.decision.families_hit ?? [],
                  funds_status: experiment ? "released" : result.funds_status,
                  gate: "none",
                  hold_until: experiment ? null : result.hold_until,
                  countdown_until: null,
                  payee: toAccount,
                  payeeName: toName,
                  amount,
                  experiment: Boolean(experiment),
                })
                if (!experiment && engineLevel === 1) {
                  setBanner(reasonsVi.join(" "))
                  return
                }
                router.replace("/(main)/transfer/result")
              },
            },
          )
        }}
      />
      )}
      {transfer.isError ? <Text style={{ color: colors.danger }}>{transfer.error.message}</Text> : null}
    </Screen>
  )
}

export function ResultScreen() {
  const router = useRouter()
  const outcome = useBank((state) => state.outcome)
  const choice = useChoice()
  const shownAt = useRef(Date.now())
  const type = useType()
  const [phase, setPhase] = useState<"choose" | "paused" | "done" | "cancelled">("choose")
  if (!outcome) return <Screen call><Header title={copy.success} /></Screen>
  if (outcome.experiment) {
    return (
      <Screen call>
        <View style={{ alignItems: "center", gap: 20, paddingVertical: 40 }}>
          <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700", textAlign: "center" }}>{copy.experimentRecorded}</Text>
          <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
        </View>
      </Screen>
    )
  }
  function pick(next: "pause" | "cancel" | "continue") {
    choice.mutate(
      { decisionId: outcome?.decisionId ?? "", choice: next, responseMs: Math.max(0, Date.now() - shownAt.current) },
      {
        onSuccess: () => setPhase(next === "pause" ? "paused" : next === "cancel" ? "cancelled" : "done"),
      },
    )
  }
  if (outcome.engineLevel === 3 || outcome.intervention === "security_workflow") {
    return (
      <Screen call>
        <View testID="level-3" style={{ gap: 16 }}>
          <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.level3Title}</Text>
          <Text style={{ color: colors.navy, fontSize: type.body }}>{copy.level3Mock}</Text>
          {outcome.reasonsVi.map((line) => <Text key={line} style={{ color: colors.navy, fontSize: type.body }}>{line}</Text>)}
          <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
        </View>
      </Screen>
    )
  }
  if (outcome.engineLevel === 2 || outcome.intervention === "three_button") {
    return (
      <Screen call>
        <View testID="level-2" style={{ gap: 16 }}>
          <Text style={{ color: colors.navy, fontSize: type.body }}>{copy.level2Body}</Text>
          {outcome.reasonsVi.map((line) => <Text key={line} style={{ color: colors.navy, fontSize: type.body }}>{line}</Text>)}
          <Text style={{ color: colors.deep, fontWeight: "700" }}>{vnd(outcome.amount)} · {outcome.payeeName}</Text>
          {phase === "choose" ? (
            <>
              <Button testID="choice-pause" label={copy.level2Title} onPress={() => pick("pause")} />
              <Button testID="choice-cancel" outline label={copy.level2Cancel} onPress={() => pick("cancel")} />
              <Button testID="choice-continue" outline label={copy.level2Continue} onPress={() => pick("continue")} />
            </>
          ) : null}
          {phase === "paused" ? (
            <>
              <Text style={{ color: colors.navy, fontSize: type.body }}>{copy.pausedDraft}</Text>
              <Text testID="bank-hotline" selectable style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{copy.hotline}</Text>
            </>
          ) : null}
          {phase === "cancelled" ? <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.cancelledTransfer}</Text> : null}
          {phase === "done" ? <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.success}</Text> : null}
          {phase !== "choose" ? <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} /> : null}
          {choice.isError ? <Text style={{ color: colors.danger }}>{choice.error.message}</Text> : null}
        </View>
      </Screen>
    )
  }
  if (outcome.status !== "done") {
    return (
      <Screen call>
        <Text style={{ color: colors.navy, fontSize: type.body }}>{outcome.reasonsVi.join(" ")}</Text>
        <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
      </Screen>
    )
  }
  return (
    <Screen call>
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

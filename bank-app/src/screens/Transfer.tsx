import { useEffect, useRef, useState } from "react"
import { InputAccessoryView, Pressable, Text, TextInput, View } from "react-native"
import { useRouter } from "expo-router"
import { Camera, Check, ChevronDown, Contact, Image as ImageIcon, QrCode, Search, ShieldCheck, X } from "lucide-react-native"
import { FidesCallNotice, FidesSession, sessionRecorder, useAccountInput } from "fides-sdk"

import { BANK_URL } from "../api/client"
import { useAccount, useBeneficiaries, useChoice, usePurposePolicy, useTransfer } from "../api/queries"
import { GUIDED_FEATURES } from "../dev/guided"
import { rememberSession } from "../dev/lastSession"
import { uploadExperiment } from "../dev/upload"
import { copy } from "../copy/vi"
import { digits, grouped, parseTransferDraft, vnd } from "../format"
import { displayHolder, transferNote } from "../names"
import { personas } from "../personas"
import { DEMO, useBank, type EntryDetail } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

const SHOP = { account: "A0119434", name: "CONG TY TNHH THANH CONG" }
const MOCK_OCR = "ZANH000000000099"

function ensureSession(task: "new_typed" | "saved_payee" | "qr") {
  if (sessionRecorder.isOpen()) return
  FidesSession.start()
  sessionRecorder.start(task)
  useBank.getState().setEntryDetail(null)
}

export function openShopQr(router: { push: (href: string) => void }) {
  ensureSession("qr")
  sessionRecorder.note({ type: "qr_scan" })
  useBank.getState().setEntryDetail(null)
  useBank.getState().setPayee(SHOP.account, SHOP.name)
  router.push("/(main)/transfer/amount")
}

export function RecipientScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const setPayee = useBank((state) => state.setPayee)
  const setEntryDetail = useBank((state) => state.setEntryDetail)
  const saved = useBeneficiaries(accountId)
  const input = useAccountInput()
  const [query, setQuery] = useState("")
  const [method, setMethod] = useState(0)
  const [bank, setBank] = useState<string>(copy.bankName)
  const type = useType()
  const rows = saved.data?.beneficiaries ?? []
  const recent = rows.filter((item) => item.times_paid > 0).slice(0, 6)
  const filtered = rows.filter((item) => `${item.holder_name} ${item.account_id}`.toLowerCase().includes(query.trim().toLowerCase()))
  useEffect(() => {
    ensureSession("new_typed")
    sessionRecorder.note({ type: "screen_enter", screen: "hub" })
  }, [])
  function go(account: string, name: string, savedPick: boolean) {
    if (savedPick) sessionRecorder.note({ type: "saved_payee_pick" })
    setEntryDetail(null)
    setPayee(account, name)
    router.push("/(main)/transfer/amount")
  }
  function fill(text: string, detail: EntryDetail) {
    const parsed = parseTransferDraft(text)
    if (!parsed.account) return
    input.onChangeText(parsed.account)
    setBank(parsed.bank)
    setEntryDetail(detail)
  }
  function submit() {
    const account = input.value.trim()
    if (!account) return
    if (method === 3 && recent[0]) {
      go(recent[0].account_id, displayHolder(recent[0].holder_name), true)
      return
    }
    setPayee(account, account)
    router.push("/(main)/transfer/amount")
  }
  const numeric = method === 1 || method === 2
  return (
    <Screen call>
      <Header title={copy.transferHub} tools />
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
        {copy.methods.map((name, index) => (
          <Pressable key={name} testID={index === 0 ? "new-account-open" : `method-${index}`} accessibilityRole="button" onPress={() => { setMethod(index); if (index === 3 && recent[0]) go(recent[0].account_id, displayHolder(recent[0].holder_name), true) }} style={{ borderRadius: 12, backgroundColor: method === index ? colors.deep : colors.mint, paddingHorizontal: 12, paddingVertical: 10 }}>
            <Text style={{ color: method === index ? colors.white : colors.deep, fontWeight: "700", fontSize: type.small }}>{name}</Text>
          </Pressable>
        ))}
      </View>
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <TextInput testID="new-account" value={input.value} onChangeText={(value) => { input.onChangeText(value); if (value.length - input.value.length <= 1) setEntryDetail(null) }} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "account" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "account" })} placeholder={copy.smartPlaceholder} keyboardType={numeric ? "number-pad" : "default"} style={{ flex: 1, color: colors.navy, fontSize: type.body }} />
          <Pressable testID="scan-shop-qr" accessibilityRole="button" accessibilityLabel="Quét mã QR cửa hàng" onPress={() => openShopQr(router)} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}>
            <QrCode size={20} color={colors.deep} />
          </Pressable>
        </View>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{bank}</Text>
        <Button testID="new-account-go" label={copy.continueTransfer} disabled={!input.value.trim() && method !== 3} onPress={submit} />
        <View style={{ flexDirection: "row", gap: 8 }}>
          <Pressable testID="capture-image" accessibilityRole="button" onPress={() => fill(MOCK_OCR, "image_capture")} style={{ flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center", gap: 4 }}>
            <Camera size={16} color={colors.deep} />
            <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>{copy.capture}</Text>
          </Pressable>
          <Pressable testID="upload-image" accessibilityRole="button" onPress={() => fill(`BVC ${MOCK_OCR}`, "image_upload")} style={{ flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center", gap: 4 }}>
            <ImageIcon size={16} color={colors.deep} />
            <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>{copy.upload}</Text>
          </Pressable>
          <Pressable testID="paste-account" accessibilityRole="button" onPress={() => fill("STK ZVAY000000000001 BVC bank", "paste")} style={{ flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700", fontSize: type.small }}>{copy.paste}</Text>
          </Pressable>
        </View>
      </Card>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.recentPayees}</Text>
      <View testID="recent-payees" style={{ flexDirection: "row", gap: 12 }}>
        {recent.map((item) => (
          <Pressable key={item.id} accessibilityRole="button" onPress={() => go(item.account_id, displayHolder(item.holder_name), true)} style={{ alignItems: "center", gap: 4 }}>
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
              <Text style={{ color: colors.deep, fontWeight: "700" }}>{displayHolder(item.holder_name).slice(0, 1)}</Text>
            </View>
          </Pressable>
        ))}
        <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.deep, fontWeight: "700" }}>{recent.length}</Text>
        </View>
      </View>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.partners}</Text>
      <View testID="partners" style={{ flexDirection: "row", gap: 8 }}>
        {copy.partnerNames.map((name) => (
          <View key={name} style={{ flex: 1, alignItems: "center", gap: 6 }}>
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.mint }} />
            <Text style={{ color: colors.deep, fontSize: 10, textAlign: "center" }}>{name}</Text>
          </View>
        ))}
      </View>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.savedPayees}</Text>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: "#F8FAFC", paddingHorizontal: 12, paddingVertical: 12 }}>
        <Search size={17} color="#94A3B8" />
        <TextInput testID="payee-search" value={query} onChangeText={setQuery} placeholder={copy.search} style={{ flex: 1, color: colors.navy, fontSize: type.body }} />
      </View>
      {filtered.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptyPayee}</Text> : null}
      {filtered.map((item) => (
        <Pressable key={item.id} accessibilityRole="button" onPress={() => go(item.account_id, displayHolder(item.holder_name), true)} style={{ flexDirection: "row", alignItems: "center", gap: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12 }}>
          <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: colors.mint, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{displayHolder(item.holder_name).slice(0, 1)}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{displayHolder(item.holder_name)}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.bankName} · {item.account_id}</Text>
          </View>
        </Pressable>
      ))}
      <Pressable testID="add-payee" accessibilityRole="button" onPress={() => setMethod(0)} style={{ minHeight: 44, justifyContent: "center" }}>
        <Text style={{ color: colors.deep, fontWeight: "700" }}>+ {copy.addPayee}</Text>
      </Pressable>
    </Screen>
  )
}

export function NewAccountScreen() {
  const router = useRouter()
  useEffect(() => { router.replace("/(main)/transfer/recipient") }, [router])
  return null
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

function mask(id: string) {
  return `•••• ${id.slice(-4)}`
}

export function AmountScreen() {
  const router = useRouter()
  const role = useBank((state) => state.role)
  const accountId = useBank((state) => state.accountId) ?? ""
  const setAccount = useBank((state) => state.setAccount)
  const toAccount = useBank((state) => state.toAccount)
  const toName = useBank((state) => state.toName)
  const setPayee = useBank((state) => state.setPayee)
  const amount = useBank((state) => state.amount)
  const setAmount = useBank((state) => state.setAmount)
  const note = useBank((state) => state.note)
  const setNote = useBank((state) => state.setNote)
  const purpose = useBank((state) => state.purpose)
  const setPurpose = useBank((state) => state.setPurpose)
  const saved = useBeneficiaries(accountId)
  const policy = usePurposePolicy()
  const source = useAccount(accountId)
  const looked = useAccount(toAccount || null)
  const [openAccounts, setOpenAccounts] = useState(false)
  const [amountFocused, setAmountFocused] = useState(false)
  const amountRef = useRef<TextInput>(null)
  const [dest, setDest] = useState(toAccount)
  const type = useType()
  const person = personas.find((item) => item.role === role)
  const ids = person?.account_ids ?? (accountId ? [accountId] : [])
  useEffect(() => {
    ensureSession("new_typed")
    sessionRecorder.note({ type: "screen_enter", screen: "form" })
    if (!useBank.getState().note && person) setNote(transferNote(person.full_name))
  }, [person, setNote])
  useEffect(() => {
    const holder = looked.data?.holder_name
    if (!holder) return
    const shown = displayHolder(holder)
    if (shown !== toName) setPayee(toAccount, shown)
  }, [looked.data?.holder_name, setPayee, toAccount, toName])
  const known = (saved.data?.beneficiaries ?? []).some((item) => item.account_id === toAccount)
  const threshold = policy.data?.purpose_amount_vnd ?? 10_000_000
  const askPurpose = saved.isSuccess && (!known || amount >= threshold)
  const ready = amount >= 1 && Boolean(toAccount) && (!askPurpose || Boolean(purpose)) && saved.isSuccess && !policy.isPending
  function onAmount(value: string) {
    const next = digits(value)
    const prev = digits(String(amount))
    if (next.length < prev.length) sessionRecorder.note({ type: "field_edit" })
    else if (next.length - prev.length > 1) sessionRecorder.note({ type: "paste" })
    else if (next.length > prev.length) sessionRecorder.note({ type: "key_tick" })
    setAmount(Number(next) || 0)
  }
  function onDest(value: string) {
    const prev = dest
    setDest(value)
    if (value.length < prev.length) sessionRecorder.note({ type: "field_edit", payee: true })
    else if (value.length - prev.length > 1) sessionRecorder.note({ type: "paste", payee: true })
    else if (value.length > prev.length) sessionRecorder.note({ type: "key_tick", payee: true })
    setPayee(value.trim(), value.trim())
  }
  function onNote(value: string) {
    if (value.length < note.length) sessionRecorder.note({ type: "field_edit" })
    else if (value.length - note.length > 1) sessionRecorder.note({ type: "paste" })
    else if (value.length > note.length) sessionRecorder.note({ type: "key_tick" })
    setNote(value)
  }
  const kind = copy.accountKind[source.data?.account_type ?? "personal"] ?? copy.balance
  return (
    <Screen call>
      <Header title={copy.transferForm} tools />
      <AmountNotice />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.source}</Text>
        <Pressable testID="source-switch" accessibilityRole="button" onPress={() => setOpenAccounts(!openAccounts)} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <View>
            <Text style={{ color: colors.navy, fontWeight: "700" }}>{kind}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>{mask(accountId)}</Text>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{vnd(source.data?.balance ?? 0)}</Text>
          </View>
          <ChevronDown size={18} color={colors.deep} />
        </Pressable>
        {openAccounts ? ids.map((id) => (
          <Pressable key={id} testID={`source-${id}`} accessibilityRole="button" onPress={() => { setAccount(id); setOpenAccounts(false) }}>
            <Text style={{ color: id === accountId ? colors.deep : colors.navy, fontWeight: "700" }}>{mask(id)}</Text>
          </Pressable>
        )) : null}
      </Card>
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.destination}</Text>
        <Pressable testID="dest-bank" accessibilityRole="button" style={{ minHeight: 44, justifyContent: "center" }}>
          <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.bankName}</Text>
        </Pressable>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <TextInput testID="dest-account" value={dest} onChangeText={onDest} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "account" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "account" })} keyboardType="number-pad" style={{ flex: 1, color: colors.navy, fontSize: type.body }} />
          <Pressable testID="dest-contacts" accessibilityRole="button" onPress={() => router.push("/(main)/transfer/recipient")}>
            <Contact size={18} color={colors.deep} />
          </Pressable>
        </View>
        {toName ? <Text style={{ color: colors.navy, fontWeight: "700" }}>{displayHolder(toName)}</Text> : null}
      </Card>
      <TextInput ref={amountRef} testID="amount" inputAccessoryViewID="amount-bar" value={amountFocused ? (amount ? String(amount) : "") : grouped(amount)} onChangeText={onAmount} onFocus={() => { setAmountFocused(true); sessionRecorder.note({ type: "field_focus", field: "amount" }) }} onBlur={() => { setAmountFocused(false); sessionRecorder.note({ type: "field_blur", field: "amount" }) }} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 36, fontWeight: "700", textAlign: "center" }} />
      <InputAccessoryView nativeID="amount-bar">
        <View style={{ backgroundColor: "#F8FAFC", borderTopWidth: 1, borderTopColor: "#E2E8F0", alignItems: "flex-end" }}>
          <Pressable testID="amount-dismiss" accessibilityRole="button" onPress={() => amountRef.current?.blur()} style={{ paddingHorizontal: 16, paddingVertical: 10 }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>Xong</Text>
          </Pressable>
        </View>
      </InputAccessoryView>
      <Text style={{ color: "#64748B", fontSize: type.small, textAlign: "center" }}>{copy.dong}</Text>
      <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.noteField}</Text>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, borderWidth: 1, borderColor: "#E2E8F0", paddingHorizontal: 16 }}>
        <TextInput testID="transfer-note" value={note} onChangeText={onNote} onFocus={() => sessionRecorder.note({ type: "field_focus", field: "note" })} onBlur={() => sessionRecorder.note({ type: "field_blur", field: "note" })} style={{ flex: 1, color: colors.navy, fontSize: type.body, paddingVertical: 12 }} />
        <Pressable testID="note-clear" accessibilityRole="button" onPress={() => setNote("")}><X size={16} color={colors.deep} /></Pressable>
      </View>
      {askPurpose ? (
        <View style={{ gap: 8 }}>
          <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.purposeQuestion}</Text>
          {copy.purposes.map(([id, label]) => (
            <Pressable key={id} testID={`purpose-${id}`} accessibilityRole="button" onPress={() => setPurpose(id)} style={{ minHeight: 44, justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: purpose === id ? colors.deep : "#E2E8F0", backgroundColor: purpose === id ? colors.mint : colors.white, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: colors.navy, fontSize: type.body }}>{label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: "row", gap: 8 }}>
        <View style={{ flex: 1 }}><Button outline label={copy.back} onPress={() => router.back()} /></View>
        <View style={{ flex: 1 }}><Button testID="continue-transfer" label={copy.continueTransfer} disabled={!ready} onPress={() => router.push("/(main)/transfer/confirm")} /></View>
      </View>
    </Screen>
  )
}

function EntryDetailLine() {
  const detail = useBank((state) => state.entryDetail)
  const type = useType()
  if (!detail) return null
  return <Text testID="entry-detail" style={{ color: colors.navy, fontSize: type.body }}>{copy.entryWay}: {copy.entryDetail[detail]}</Text>
}

export function ConfirmScreen() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId) ?? ""
  const toAccount = useBank((state) => state.toAccount)
  const toName = useBank((state) => state.toName)
  const amount = useBank((state) => state.amount)
  const note = useBank((state) => state.note)
  const purpose = useBank((state) => state.purpose)
  const entryDetail = useBank((state) => state.entryDetail)
  const setOutcome = useBank((state) => state.setOutcome)
  const devCapture = useBank((state) => state.devCapture)
  const setDevCapture = useBank((state) => state.setDevCapture)
  const devGuided = useBank((state) => state.devGuided)
  const setDevGuided = useBank((state) => state.setDevGuided)
  const setStrangerCall = useBank((state) => state.setStrangerCall)
  const transfer = useTransfer()
  const saved = useBeneficiaries(accountId)
  const policy = usePurposePolicy()
  const type = useType()
  const [banner, setBanner] = useState<string | null>(null)
  useEffect(() => { sessionRecorder.note({ type: "screen_enter", screen: "confirm" }) }, [])
  const known = (saved.data?.beneficiaries ?? []).some((item) => item.account_id === toAccount)
  const threshold = policy.data?.purpose_amount_vnd ?? 10_000_000
  const askPurpose = saved.isSuccess && (!known || amount >= threshold)
  const waiting = !saved.isSuccess || policy.isPending
  return (
    <Screen call>
      <Header title={copy.confirm} tools />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.payee}</Text>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{toName}</Text>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.moneyLabel}</Text>
        <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{vnd(amount)}</Text>
        <EntryDetailLine />
      </Card>
      {amount >= 10_000_000 ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <ShieldCheck size={16} color={colors.warn} />
          <Text style={{ color: colors.warn, fontSize: type.small }}>{copy.face}</Text>
        </View>
      ) : null}
      {DEMO ? (
        <View style={{ gap: 8 }}>
          <Pressable testID="dev-active-capture" accessibilityRole="button" onPress={() => setDevCapture(!devCapture)} style={{ minHeight: 44, justifyContent: "center" }}>
            <Text style={{ color: devCapture ? colors.warn : colors.deep, fontWeight: "700" }}>Giả lập demo: màn hình đang bị ghi</Text>
          </Pressable>
          <Pressable testID="dev-guided" accessibilityRole="button" onPress={() => setDevGuided(!devGuided)} style={{ minHeight: 44, justifyContent: "center" }}>
            <Text style={{ color: devGuided ? colors.warn : colors.deep, fontWeight: "700" }}>Phiên có hướng dẫn (demo)</Text>
          </Pressable>
          <Pressable testID="call-end" accessibilityRole="button" onPress={() => { FidesSession.setSimulatedCall(false, false); setStrangerCall(false) }} style={{ minHeight: 44, justifyContent: "center" }}>
            <Text style={{ color: colors.deep, fontWeight: "700" }}>Kết thúc cuộc gọi giả lập</Text>
          </Pressable>
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
          const behaviour = devGuided ? GUIDED_FEATURES : session.behaviour_features
          const device = { ...FidesSession.collectSignals(), state: (devCapture ? "active_capture" : "unknown") as "active_capture" | "unknown" }
          rememberSession({ task_type: session.task_type, call, behaviour_features: behaviour })
          const experiment = session.experiment
          if (experiment && session.raw_events) {
            void uploadExperiment({
              participant_id: experiment.participant_id,
              condition: experiment.condition,
              session_index: experiment.session_index,
              task_type: session.task_type,
              call,
              behaviour_features: behaviour,
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
              behaviour_features: behaviour,
              transfer_purpose: askPurpose && purpose ? purpose : "not_asked",
              entry_detail: entryDetail ?? undefined,
              device,
              experiment: session.experiment,
            },
            {
              onSuccess: (result) => {
                const card = result.decision.evidence_card
                if (card && entryDetail) card.entry_detail = entryDetail
                if (experiment) {
                  console.log(JSON.stringify({
                    experiment,
                    level: result.decision.level,
                    engine_level: result.decision.engine_level ?? null,
                    rules_fired: result.decision.rules_fired ?? [],
                    evidence_card: card ?? null,
                  }))
                }
                const engineLevel = result.decision.engine_level ?? 0
                const reasonsVi = card?.reasons_vi ?? []
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
          <EntryDetailLine />
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
          <EntryDetailLine />
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
        <EntryDetailLine />
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

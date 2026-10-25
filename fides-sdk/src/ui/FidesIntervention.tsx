import { useEffect, useMemo, useState, type ReactNode } from "react"
import { Pressable, Text, View } from "react-native"
import Animated, { FadeIn } from "react-native-reanimated"
import { AlertTriangle, CheckCircle2, Clock3, Phone, Shield, ShieldCheck, UserRound } from "lucide-react-native"

import type { components } from "../generated/openapi"
import { copyVi as c } from "./copy.vi"
import { capPrimary, mergeFidesTheme, type FidesThemeOverride, type ResolvedTheme } from "./theme"
import { fidesScreen, formatHold, formatRemaining, remainingSeconds, type FidesDecision } from "./screen"

type RespondRequest = components["schemas"]["RespondRequest"]
type WhoGuides = components["schemas"]["WhoGuides"]

export type FidesRespond = (body: RespondRequest) => void
export type FidesProps = {
  decision: FidesDecision
  onRespond: FidesRespond
  theme?: FidesThemeOverride
  largeText?: boolean
}

const WHO: { value: WhoGuides; label: (typeof c.guides)[number] }[] = [
  { value: "authority", label: c.guides[0] },
  { value: "bank", label: c.guides[1] },
  { value: "relative", label: c.guides[2] },
  { value: "friend", label: c.guides[3] },
  { value: "seller", label: c.guides[4] },
  { value: "nobody", label: c.guides[5] },
  { value: "other", label: c.guides[6] },
]

function respond(onRespond: FidesRespond, decisionId: string, extra: Omit<RespondRequest, "decision_id"> = {}) {
  onRespond({ decision_id: decisionId, ...extra })
}

function Title({ children, colors }: { children: string; colors: ResolvedTheme }) {
  return <Text style={{ color: colors.colors.navy, fontSize: colors.type.title, fontWeight: "700", lineHeight: colors.type.title * 1.3 }}>{children}</Text>
}

function Body({ children, colors }: { children: string; colors: ResolvedTheme }) {
  return <Text style={{ color: colors.colors.navy, fontSize: colors.type.body, lineHeight: colors.type.line }}>{children}</Text>
}

function Transaction({ decision, colors }: { decision: FidesDecision; colors: ResolvedTheme }) {
  if (!decision.channel && !decision.payee_name && !decision.amount_text) return null
  return (
    <View className="flex-col gap-1 rounded-2xl bg-mint p-4" style={{ gap: 4, borderRadius: colors.radius.card, backgroundColor: colors.colors.mint, padding: 16 }}>
      {decision.channel ? <Text style={{ color: colors.colors.deep, fontSize: colors.type.small }}>{decision.channel}</Text> : null}
      <View className="flex-row items-center justify-between gap-3" style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <Text style={{ color: colors.colors.navy, fontWeight: "600", fontSize: colors.type.body, flex: 1 }}>{decision.payee_name}</Text>
        <Text style={{ color: colors.colors.deep, fontWeight: "700", fontSize: colors.type.body }}>{decision.amount_text}</Text>
      </View>
    </View>
  )
}

function Clock({ until, colors }: { until: string | null | undefined; colors: ResolvedTheme }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!until) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [until])
  const seconds = remainingSeconds(until, now)
  if (seconds == null) return null
  return (
    <View className="items-center gap-2 rounded-2xl bg-mint p-6" style={{ alignItems: "center", gap: 8, borderRadius: colors.radius.card, backgroundColor: colors.colors.mint, padding: 24 }}>
      <Clock3 color={colors.colors.deep} />
      <Animated.Text entering={FadeIn} style={{ color: colors.colors.deep, fontSize: colors.type.countdown, fontWeight: "700" }}>
        {formatRemaining(seconds)}
      </Animated.Text>
    </View>
  )
}

type ButtonSpec = { key: string; label: string; onPress: () => void; fill: string; labelColor: string; border?: string }

function Actions({ items, colors, largeText = false, stack = false }: { items: ButtonSpec[]; colors: ResolvedTheme; largeText?: boolean; stack?: boolean }) {
  const shown = capPrimary(items, largeText)
  return (
    <View className="flex-row gap-3" style={{ flexDirection: stack ? "column" : "row", gap: 12 }}>
      {shown.map((item) => (
        <Pressable
          key={item.key}
          accessibilityRole="button"
          onPress={item.onPress}
          className="min-h-11 flex-1 items-center justify-center rounded-xl px-4"
          style={{
            minHeight: 44,
            flex: stack ? undefined : 1,
            alignItems: "center",
            justifyContent: "center",
            borderRadius: colors.radius.button,
            paddingHorizontal: 16,
            paddingVertical: 12,
            backgroundColor: item.fill,
            borderWidth: item.border ? 1 : 0,
            borderColor: item.border,
          }}
        >
          <Text style={{ color: item.labelColor, fontSize: colors.type.small, fontWeight: "600", textAlign: "center" }}>{item.label}</Text>
        </Pressable>
      ))}
    </View>
  )
}

function Frame({ testID, children }: { testID: string; children: ReactNode }) {
  return (
    <View testID={testID} className="flex-col gap-5" style={{ gap: 20 }}>
      {children}
    </View>
  )
}

function Info({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-info">
      <CheckCircle2 color={colors.colors.aqua} size={42} />
      <Title colors={colors}>{c.info.title}</Title>
      <Body colors={colors}>{c.info.body}</Body>
      <Transaction decision={decision} colors={colors} />
      <Actions
        colors={colors}
        largeText={largeText}
        items={[
          { key: "yes", label: c.info.yes, onPress: () => respond(onRespond, decision.decision_id, { proceed: true }), fill: colors.colors.deep, labelColor: colors.colors.white },
          { key: "no", label: c.info.no, onPress: () => respond(onRespond, decision.decision_id, { cancel: true }), fill: colors.colors.white, labelColor: colors.colors.danger, border: colors.colors.danger },
        ]}
      />
    </Frame>
  )
}

function Banner({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-banner">
      <Title colors={colors}>{c.info.title}</Title>
      <View className="gap-3 rounded-2xl bg-warnSoft p-5" style={{ gap: 12, borderRadius: colors.radius.card, backgroundColor: colors.colors.warnSoft, padding: 20 }}>
        <Text style={{ color: colors.colors.navy, fontWeight: "800", fontSize: colors.type.body }}>{c.banner.title}</Text>
        <Body colors={colors}>{c.banner.body}</Body>
      </View>
      <Transaction decision={decision} colors={colors} />
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "confirm", label: c.confirm, onPress: () => respond(onRespond, decision.decision_id, { proceed: true }), fill: colors.colors.deep, labelColor: colors.colors.white }]}
      />
    </Frame>
  )
}

function Simo({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-simo_listed">
      <Title colors={colors}>{c.info.title}</Title>
      <View className="gap-3 rounded-2xl bg-dangerSoft p-5" style={{ gap: 12, borderRadius: colors.radius.card, backgroundColor: colors.colors.dangerSoft, padding: 20 }}>
        <Text style={{ color: colors.colors.danger, fontWeight: "800", fontSize: colors.type.body }}>{c.banner.title}</Text>
        <Text style={{ color: colors.colors.navy, fontSize: colors.type.body, lineHeight: colors.type.body * 1.45 }}>{c.simo}</Text>
      </View>
      <Actions
        colors={colors}
        largeText={largeText}
        items={[
          { key: "cancel", label: c.cancel, onPress: () => respond(onRespond, decision.decision_id, { cancel: true }), fill: colors.colors.danger, labelColor: colors.colors.white },
          { key: "confirm", label: c.confirm, onPress: () => respond(onRespond, decision.decision_id, { proceed: true }), fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep },
        ]}
      />
    </Frame>
  )
}

function Pause({ decision, onRespond, colors }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-pause_and_verify">
      <AlertTriangle color={colors.colors.warn} size={42} />
      <Title colors={colors}>{c.pause.title}</Title>
      <Body colors={colors}>{c.pause.sub}</Body>
      <Text style={{ color: colors.colors.navy, fontSize: colors.type.title * 1.25, fontWeight: "800", lineHeight: colors.type.title * 1.4 }}>{c.pause.question}</Text>
      <Transaction decision={decision} colors={colors} />
      <View className="flex-col gap-3" style={{ gap: 12 }}>
        {WHO.map((guide) => (
          <Pressable
            key={guide.value}
            accessibilityRole="button"
            onPress={() => respond(onRespond, decision.decision_id, { who_guides: guide.value })}
            className="min-h-11 justify-center rounded-xl border border-deep px-4"
            style={{ minHeight: 44, justifyContent: "center", borderRadius: colors.radius.button, borderWidth: 1, borderColor: colors.colors.deep, paddingHorizontal: 16, paddingVertical: 10 }}
          >
            <Text style={{ color: colors.colors.navy, fontSize: colors.type.body }}>{guide.label}</Text>
          </Pressable>
        ))}
      </View>
      <Pressable accessibilityRole="button" onPress={() => respond(onRespond, decision.decision_id, { cancel: true })}>
        <Text style={{ color: colors.colors.deep, fontWeight: "700", textAlign: "center" }}>{c.cancel}</Text>
      </Pressable>
    </Frame>
  )
}

function Chain({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  const rows = decision.chain_rows?.length ? decision.chain_rows : [...c.chain.steps]
  return (
    <Frame testID="fides-show_chain">
      <Title colors={colors}>{c.chain.title}</Title>
      <Body colors={colors}>{c.chain.body}</Body>
      <Transaction decision={decision} colors={colors} />
      <View className="flex-col gap-2 rounded-2xl border border-aqua p-4" style={{ gap: 8, borderRadius: colors.radius.card, borderWidth: 1, borderColor: colors.colors.aqua, padding: 16 }}>
        {rows.map((row) => (
          <Text key={row} style={{ color: colors.colors.navy, fontSize: colors.type.body }}>{row}</Text>
        ))}
      </View>
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "confirm", label: c.chain.confirm, onPress: () => respond(onRespond, decision.decision_id, { proceed: true }), fill: colors.colors.deep, labelColor: colors.colors.white }]}
      />
    </Frame>
  )
}

function GuardianWait({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-guardian_confirm_requested">
      <UserRound color={colors.colors.deep} size={42} />
      <Title colors={colors}>{c.guardian.pending}</Title>
      <Body colors={colors}>{c.guardian.body}</Body>
      <Transaction decision={decision} colors={colors} />
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "close", label: c.close, onPress: () => respond(onRespond, decision.decision_id), fill: colors.colors.deep, labelColor: colors.colors.white }]}
      />
    </Frame>
  )
}

function Wait({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-short_wait">
      <Title colors={colors}>{c.wait.title}</Title>
      <Clock until={decision.countdown_until} colors={colors} />
      <Body colors={colors}>{c.wait.body}</Body>
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "continue", label: c.continue, onPress: () => respond(onRespond, decision.decision_id, { proceed: true }), fill: colors.colors.deep, labelColor: colors.colors.white }]}
      />
    </Frame>
  )
}

function Hangup({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-hangup_required">
      <AlertTriangle color={colors.colors.danger} size={42} />
      <Title colors={colors}>{c.hangup.title}</Title>
      <Body colors={colors}>{c.hangup.body}</Body>
      <Transaction decision={decision} colors={colors} />
      {(decision.reasons ?? []).map((reason) => (
        <Text key={reason.code} style={{ color: colors.colors.navy, fontSize: colors.type.body }}>{reason.text}</Text>
      ))}
      <Actions
        colors={colors}
        largeText={largeText}
        stack
        items={[
          { key: "call", label: c.hangup.call, onPress: () => respond(onRespond, decision.decision_id, { hung_up: true }), fill: colors.colors.danger, labelColor: colors.colors.white },
          { key: "close", label: c.close, onPress: () => respond(onRespond, decision.decision_id), fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep },
        ]}
      />
    </Frame>
  )
}

function Countdown({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-countdown">
      <Title colors={colors}>{c.countdown.body}</Title>
      <Clock until={decision.countdown_until} colors={colors} />
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "close", label: c.close, onPress: () => respond(onRespond, decision.decision_id), fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep }]}
      />
    </Frame>
  )
}

function Held({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-held">
      <ShieldCheck color={colors.colors.aqua} size={42} />
      <Title colors={colors}>{c.held.title}</Title>
      <Body colors={colors}>{c.held.body}</Body>
      <Transaction decision={decision} colors={colors} />
      {decision.hold_until ? <Text style={{ color: colors.colors.warn, fontSize: colors.type.small, fontWeight: "600" }}>{c.held.expected}: {formatHold(decision.hold_until)}</Text> : null}
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "close", label: c.close, onPress: () => respond(onRespond, decision.decision_id), fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep }]}
      />
    </Frame>
  )
}

function Cancelled({ decision, onRespond, colors, largeText }: FidesProps & { colors: ResolvedTheme }) {
  return (
    <Frame testID="fides-cancelled">
      <ShieldCheck color={colors.colors.aqua} size={42} />
      <Title colors={colors}>{c.cancelled.title}</Title>
      <Body colors={colors}>{c.cancelled.body}</Body>
      <Transaction decision={decision} colors={colors} />
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "close", label: c.close, onPress: () => respond(onRespond, decision.decision_id), fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep }]}
      />
    </Frame>
  )
}

export function FidesIntervention({ decision, onRespond, theme, largeText = false }: FidesProps) {
  const colors = useMemo(() => mergeFidesTheme(theme, largeText), [theme, largeText])
  const screen = fidesScreen(decision)
  const props = { decision, onRespond, theme, largeText, colors }
  if (screen === "cancelled") return <Cancelled {...props} />
  if (screen === "countdown") return <Countdown {...props} />
  if (screen === "hangup_required") return <Hangup {...props} />
  if (screen === "short_wait") return <Wait {...props} />
  if (screen === "guardian_confirm_requested") return <GuardianWait {...props} />
  if (screen === "show_chain") return <Chain {...props} />
  if (screen === "pause_and_verify") return <Pause {...props} />
  if (screen === "simo_listed") return <Simo {...props} />
  if (screen === "banner") return <Banner {...props} />
  if (screen === "held") return <Held {...props} />
  return <Info {...props} />
}

export function FidesPermissionScreen({ onAllow, onLater, theme, largeText = false }: { onAllow: () => void; onLater: () => void; theme?: FidesThemeOverride; largeText?: boolean }) {
  const colors = useMemo(() => mergeFidesTheme(theme, largeText), [theme, largeText])
  return (
    <Frame testID="fides-permission">
      <View className="items-center gap-5 py-8" style={{ alignItems: "center", gap: 20, paddingVertical: 32 }}>
        <View className="items-center justify-center rounded-full bg-mint p-7" style={{ alignItems: "center", justifyContent: "center", borderRadius: 999, backgroundColor: colors.colors.mint, padding: 28 }}>
          <Shield size={70} color={colors.colors.blue} />
          <Phone size={30} color={colors.colors.blue} />
        </View>
        <Title colors={colors}>{c.permission.title}</Title>
        <Body colors={colors}>{c.permission.body}</Body>
      </View>
      <Actions
        colors={colors}
        largeText={largeText}
        items={[
          { key: "allow", label: c.allow, onPress: onAllow, fill: colors.colors.deep, labelColor: colors.colors.white },
          { key: "later", label: c.later, onPress: onLater, fill: colors.colors.white, labelColor: colors.colors.deep, border: colors.colors.deep },
        ]}
      />
    </Frame>
  )
}

export type FidesGuardianView = {
  decision_id: string
  sender_name?: string
  payee_name?: string
  amount_text?: string
}

export function FidesGuardianRequest({ request, onDecide, theme, largeText = false }: { request: FidesGuardianView; onDecide: FidesRespond; theme?: FidesThemeOverride; largeText?: boolean }) {
  const colors = useMemo(() => mergeFidesTheme(theme, largeText), [theme, largeText])
  const detail = request.sender_name
    ? `${request.sender_name} đang chuyển ${request.amount_text ?? c.money} đến tài khoản ${request.payee_name ?? c.recipient}.`
    : c.guardian.guardianBody
  return (
    <Frame testID="fides-guardian-request">
      <Title colors={colors}>{c.guardian.guardianTitle}</Title>
      <View className="gap-3 rounded-2xl bg-mint p-5" style={{ gap: 12, borderRadius: colors.radius.card, backgroundColor: colors.colors.mint, padding: 20 }}>
        <Text style={{ color: colors.colors.money, fontSize: colors.type.amount, fontWeight: "800" }}>{request.amount_text ?? c.money}</Text>
        <Body colors={colors}>{detail}</Body>
      </View>
      <Actions
        colors={colors}
        largeText={largeText}
        items={[{ key: "hold", label: c.guardian.approve, onPress: () => respond(onDecide, request.decision_id, { guardian_confirm: false, cancel: true }), fill: colors.colors.deep, labelColor: colors.colors.white }]}
      />
      <Pressable accessibilityRole="button" onPress={() => respond(onDecide, request.decision_id, { guardian_confirm: true })}>
        <Text style={{ color: colors.colors.deep, fontWeight: "700", textAlign: "center" }}>{c.guardian.reject}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" onPress={() => respond(onDecide, request.decision_id)}>
        <Text style={{ color: colors.colors.deep, fontWeight: "700", textAlign: "center" }}>{c.guardian.call}</Text>
      </Pressable>
    </Frame>
  )
}

import { useEffect, useState } from "react"
import { Text, View } from "react-native"

import { FidesSession } from "../FidesSession"
import { showCallNotice } from "./callNotice"
import { copyVi } from "./copy.vi"

const ENDED_MS = 10_000

function pillLabel(): string | null {
  const state = FidesSession.callState()
  if (state.in_call === true) {
    if (state.call_direction === "inbound") return copyVi.callPillIn
    if (state.call_direction === "outbound") return copyVi.callPillOut
    return copyVi.callPill
  }
  return null
}

export function FidesCallIndicator() {
  const [label, setLabel] = useState<string | null>(null)
  useEffect(() => {
    let endedAt: number | null = null
    const read = () => {
      const live = pillLabel()
      if (live) {
        endedAt = null
        setLabel(live)
        return
      }
      if (FidesSession.callEndedInSession()) {
        if (endedAt === null) endedAt = Date.now()
        setLabel(Date.now() - endedAt < ENDED_MS ? copyVi.callEnded : null)
        return
      }
      endedAt = null
      setLabel(null)
    }
    read()
    const stop = FidesSession.onCallStateChanged(read)
    const timer = setInterval(read, 1000)
    return () => {
      stop()
      clearInterval(timer)
    }
  }, [])
  if (!label) return null
  return (
    <View testID="call-pill" style={{ alignSelf: "flex-start", borderRadius: 999, backgroundColor: "#F1F5F9", paddingHorizontal: 10, paddingVertical: 4 }}>
      <Text style={{ color: "#334155", fontSize: 12, fontWeight: "600" }}>{label}</Text>
    </View>
  )
}

export function FidesCallNotice({ inCall, used, max, demo }: { inCall: boolean; used: number; max: number; demo: boolean }) {
  if (!showCallNotice(inCall, used, max, demo)) return null
  return (
    <View testID="call-notice" style={{ borderRadius: 12, backgroundColor: "#E8F4FB", paddingHorizontal: 12, paddingVertical: 10 }}>
      <Text style={{ color: "#0B2A4A", fontSize: 13 }}>{copyVi.callNotice}</Text>
    </View>
  )
}

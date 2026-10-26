import { useEffect, useRef, useState } from "react"
import { Pressable, Text, View } from "react-native"
import { ShieldCheck } from "lucide-react-native"
import { FidesSession } from "fides-sdk"

import { BANK_URL } from "../api/client"
import { copy } from "../copy/vi"
import { DEMO } from "../store"
import { colors } from "../ui/parts"
import { CallSimulator } from "./CallSimulator"

export function JudgePanel() {
  const taps = useRef(0)
  const [open, setOpen] = useState(false)
  const [mismatch, setMismatch] = useState(false)
  useEffect(() => { FidesSession.start() }, [])
  useEffect(() => {
    let stop = false
    const read = () => {
      fetch(`${BANK_URL}/health`)
        .then((response) => response.json())
        .then((body: { ledger_mismatch?: boolean }) => {
          if (!stop) setMismatch(Boolean(body.ledger_mismatch))
        })
        .catch(() => undefined)
    }
    read()
    const timer = setInterval(read, 4000)
    return () => {
      stop = true
      clearInterval(timer)
    }
  }, [])
  return (
    <View style={{ gap: 12 }}>
      {DEMO ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={copy.logo}
          testID="sao-logo"
          onPress={() => {
            taps.current += 1
            if (taps.current >= 7) {
              taps.current = 0
              setOpen(true)
            }
          }}
          style={{ flexDirection: "row", alignItems: "center", gap: 8 }}
        >
          <ShieldCheck size={25} color={colors.deep} />
          <Text style={{ color: colors.deep, fontWeight: "700", letterSpacing: 2 }}>{copy.logo}</Text>
        </Pressable>
      ) : (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <ShieldCheck size={25} color={colors.deep} />
          <Text style={{ color: colors.deep, fontWeight: "700", letterSpacing: 2 }}>{copy.logo}</Text>
        </View>
      )}
      {mismatch ? <Text style={{ color: colors.danger, fontSize: 13 }}>{copy.ledgerMismatch}</Text> : null}
      {DEMO ? <CallSimulator open={open} onClose={() => setOpen(false)} /> : null}
    </View>
  )
}

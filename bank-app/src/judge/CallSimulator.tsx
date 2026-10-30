import { Modal, Pressable, Text, View } from "react-native"
import { FidesSession } from "fides-sdk"

import { copy } from "../copy/vi"
import { DEMO, useBank } from "../store"
import { colors } from "../ui/parts"

export function CallSimulator({ open, onClose }: { open: boolean; onClose: () => void }) {
  const outcome = useBank((state) => state.outcome)
  const on = useBank((state) => state.strangerCall)
  const setOn = useBank((state) => state.setStrangerCall)
  if (!DEMO) return null
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable accessible={false} onPress={onClose} style={{ flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(11,42,74,0.35)" }}>
        <View style={{ gap: 12, borderTopLeftRadius: 24, borderTopRightRadius: 24, backgroundColor: colors.white, padding: 20 }}>
          <Text style={{ color: colors.navy, fontSize: 20, fontWeight: "700" }}>{copy.simulator}</Text>
          {outcome?.reasons.length ? (
            <View style={{ gap: 6 }}>
              <Text style={{ color: colors.deep, fontWeight: "700" }}>{outcome.level} · {outcome.familiesHit.join(", ")}</Text>
              {outcome.reasons.map((reason) => (
                <Text key={reason.code} style={{ color: colors.navy }}>{reason.text}</Text>
              ))}
            </View>
          ) : null}
          <Pressable
            testID="call-stranger"
            accessibilityRole="button"
            onPress={() => {
              const next = !on
              setOn(next)
              FidesSession.setSimulatedCall(next, false)
              onClose()
            }}
            style={{ minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: on ? colors.warn : colors.deep }}
          >
            <Text style={{ color: colors.white, fontWeight: "700" }}>{on ? copy.strangerOff : copy.stranger}</Text>
          </Pressable>
          <Pressable
            testID="call-relative"
            accessibilityRole="button"
            onPress={() => {
              const next = !on
              setOn(next)
              FidesSession.setSimulatedCall(next, false)
              onClose()
            }}
            style={{ minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: colors.deep }}
          >
            <Text style={{ color: colors.deep, fontWeight: "700" }}>{copy.relativeCall}</Text>
          </Pressable>
        </View>
      </Pressable>
    </Modal>
  )
}

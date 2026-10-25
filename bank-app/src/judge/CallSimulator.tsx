import { Modal, Pressable, Text, View } from "react-native"
import * as Linking from "expo-linking"
import { FidesSession } from "fides-sdk"

import { copy } from "../copy/vi"
import { DEMO, useBank } from "../store"
import { colors } from "../ui/parts"

export function CallSimulator({ open, onClose }: { open: boolean; onClose: () => void }) {
  const on = useBank((state) => state.strangerCall)
  const setOn = useBank((state) => state.setStrangerCall)
  const setLarge = useBank((state) => state.setLargeText)
  if (!DEMO) return null
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable accessibilityRole="button" onPress={onClose} style={{ flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(11,42,74,0.35)" }}>
        <View style={{ gap: 12, borderTopLeftRadius: 24, borderTopRightRadius: 24, backgroundColor: colors.white, padding: 20 }}>
          <Text style={{ color: colors.navy, fontSize: 20, fontWeight: "700" }}>{copy.simulator}</Text>
          <Pressable
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
          {(["pause_and_verify", "hangup_required"] as const).map((screen) => (
            <Pressable
              key={screen}
              accessibilityRole="button"
              onPress={() => {
                setLarge(true)
                onClose()
                Linking.openURL(`saobank://fides-preview/${screen}`)
              }}
              style={{ minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: colors.deep }}
            >
              <Text style={{ color: colors.deep, fontWeight: "700" }}>{screen === "pause_and_verify" ? "Tạm dừng để kiểm tra" : "Hãy cúp máy trước khi tiếp tục"}</Text>
            </Pressable>
          ))}
        </View>
      </Pressable>
    </Modal>
  )
}

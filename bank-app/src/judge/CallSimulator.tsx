import { Pressable, Text } from "react-native"
import { FidesSession } from "fides-sdk"

import { copy } from "../copy/vi"
import { useBank } from "../store"
import { colors } from "../ui/parts"

export function CallSimulator() {
  const on = useBank((state) => state.strangerCall)
  const setOn = useBank((state) => state.setStrangerCall)
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => {
        const next = !on
        setOn(next)
        FidesSession.setSimulatedCall(next, false)
      }}
      className="rounded-xl bg-warn px-3 py-2"
      style={{ borderRadius: 12, backgroundColor: on ? colors.warn : colors.mint, paddingHorizontal: 12, paddingVertical: 8 }}
    >
      <Text style={{ color: on ? colors.white : colors.deep, fontWeight: "700" }}>{on ? copy.strangerOff : copy.stranger}</Text>
    </Pressable>
  )
}

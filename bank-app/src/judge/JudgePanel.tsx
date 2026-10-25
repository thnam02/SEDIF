import { Pressable, Text, View } from "react-native"

import { copy } from "../copy/vi"
import { DEMO, useBank } from "../store"
import { colors } from "../ui/parts"
import { CallSimulator } from "./CallSimulator"

export function JudgePanel() {
  const large = useBank((state) => state.largeText)
  const setLarge = useBank((state) => state.setLargeText)
  if (!DEMO) return null
  return (
    <View className="flex-row gap-2" style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
      <CallSimulator />
      <Pressable accessibilityRole="button" onPress={() => setLarge(!large)} style={{ borderRadius: 12, backgroundColor: large ? colors.warn : colors.mint, paddingHorizontal: 12, paddingVertical: 8 }}>
        <Text style={{ color: large ? colors.white : colors.deep, fontWeight: "700" }}>{copy.largeText}</Text>
      </Pressable>
    </View>
  )
}

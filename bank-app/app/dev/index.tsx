import { useCallback, useState } from "react"
import { Text, View } from "react-native"
import { useFocusEffect } from "expo-router"

import { readLastSession, type LastSession } from "../../src/dev/lastSession"
import { colors, Header, Screen, useType } from "../../src/ui/parts"

function lines(session: LastSession): string[] {
  const call = session.call
  const features = session.behaviour_features
  return [
    `task_type: ${session.task_type}`,
    `call.state: ${call.state}`,
    `call.observability: ${call.observability}`,
    `call.transport: ${call.transport}`,
    `median_gap_ms: ${features.median_gap_ms}`,
    `p90_gap_ms: ${features.p90_gap_ms}`,
    `burst_count: ${features.burst_count}`,
    `pause_to_action_ratio: ${features.pause_to_action_ratio}`,
    `review_dwell_ms: ${features.review_dwell_ms}`,
    `edit_count: ${features.edit_count}`,
    `backtrack_count: ${features.backtrack_count}`,
    `screen_revisit_count: ${features.screen_revisit_count}`,
    `background_count: ${features.background_count}`,
    `background_total_ms: ${features.background_total_ms}`,
    `session_duration_ms: ${features.session_duration_ms}`,
  ]
}

export default function DevSessionScreen() {
  const type = useType()
  const [session, setSession] = useState<LastSession | null>(null)
  useFocusEffect(useCallback(() => { setSession(readLastSession()) }, []))
  return (
    <Screen>
      <Header title="Phiên FIDES" />
      {session == null ? <Text style={{ color: "#64748B", fontSize: type.body }}>Chưa có phiên. Hãy xác nhận một lệnh chuyển.</Text> : null}
      {session ? (
        <View style={{ gap: 8 }}>
          {lines(session).map((line) => (
            <Text key={line} style={{ color: colors.navy, fontSize: type.body }}>{line}</Text>
          ))}
        </View>
      ) : null}
    </Screen>
  )
}

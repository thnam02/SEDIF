import { useLocalSearchParams } from "expo-router"
import { FidesIntervention } from "fides-sdk"

import { useBank } from "../../../src/store"
import { Screen } from "../../../src/ui/parts"

const held = "2026-09-20T09:30:00+07:00"

export default function FidesPreview() {
  const params = useLocalSearchParams<{ screen?: string | string[] }>()
  const screen = Array.isArray(params.screen) ? params.screen[0] : params.screen
  const largeText = useBank((state) => state.largeText)
  const hangup = screen === "hangup_required"
  return (
    <Screen>
      <FidesIntervention
        largeText={largeText}
        decision={{
          decision_id: "preview",
          level: hangup ? "high" : "medium",
          actions: hangup ? ["hangup_required"] : ["pause_and_verify"],
          reasons: hangup
            ? [{ code: "in_call", text: "Đang có cuộc gọi" }, { code: "funding", text: "Bạn vừa rút tiết kiệm" }, { code: "new_payee", text: "Người nhận là tài khoản mới" }]
            : [{ code: "review", text: "Giao dịch đang được giữ an toàn trong 30 phút" }],
          funds_status: "held",
          hold_until: held,
          countdown_until: null,
          payee_name: "LE THI C",
          amount_text: "120.000.000 đ",
          channel: "Chuyển khoản",
        }}
        onRespond={() => undefined}
      />
    </Screen>
  )
}

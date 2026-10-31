import { Text, View } from "react-native"

import { copy } from "../copy/vi"
import { isHeld, movementTitle, showAccount, vnd, when } from "../format"
import { payeeName } from "../names"
import { colors, useType } from "./parts"

const IN_COLOR = "#2E7D32"

export type LedgerItem = {
  tx_id: string
  ts: string
  direction: string
  amount: number
  tx_type: string
  status?: string
  purpose_text?: string | null
  counterparty_id?: string | null
  counterparty_name?: string | null
  balanceAfter?: number
}

export function LedgerRow({ item, showBalance = false, testID }: { item: LedgerItem; showBalance?: boolean; testID?: string }) {
  const type = useType()
  const stamp = when(item.ts)
  const inbound = item.direction === "in"
  const held = isHeld(item.status)
  const who = payeeName(item.counterparty_name, item.counterparty_id)
  const title = movementTitle({ ...item, counterparty_name: who })
  const party = item.counterparty_id ? `${copy.bankName} · ${showAccount(item.counterparty_id)}` : ""
  return (
    <View testID={testID} style={{ flexDirection: "row", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: "#F1F5F9", paddingBottom: 12, gap: 12 }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: colors.navy, fontWeight: "600", fontSize: type.body }}>{title}</Text>
        {party ? <Text style={{ color: "#64748B", fontSize: type.small }}>{party}</Text> : null}
        {showBalance ? <Text style={{ color: "#64748B", fontSize: type.small }}>{stamp.time} · {stamp.date}</Text> : null}
      </View>
      <View>
        <Text style={{ color: inbound ? IN_COLOR : colors.navy, fontWeight: "700", textAlign: "right" }}>
          {inbound ? "+" : "−"}
          {vnd(item.amount)}
          {held ? <Text style={{ color: colors.warn }}> · {copy.held}</Text> : null}
        </Text>
        {showBalance && item.balanceAfter != null ? <Text style={{ color: "#64748B", fontSize: type.small, textAlign: "right" }}>{copy.balanceAfter} {vnd(item.balanceAfter)}</Text> : null}
        {showBalance ? null : (
          <>
            <Text style={{ color: "#94A3B8", fontSize: type.small, textAlign: "right" }}>{stamp.time}</Text>
            <Text style={{ color: "#94A3B8", fontSize: type.small, textAlign: "right" }}>{stamp.date}</Text>
          </>
        )}
      </View>
    </View>
  )
}

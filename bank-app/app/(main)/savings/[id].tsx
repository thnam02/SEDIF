import { Text } from "react-native"
import { useLocalSearchParams } from "expo-router"

import { useHistory } from "../../../src/api/queries"
import { vnd } from "../../../src/format"
import { useBank } from "../../../src/store"
import { Card, colors, Header, Screen } from "../../../src/ui/parts"
import { copy } from "../../../src/copy/vi"

export default function SavingsDetail() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const accountId = useBank((state) => state.accountId)
  const history = useHistory(accountId)
  const book = (history.data?.entries ?? []).find((item) => item.tx_id === id)
  return (
    <Screen>
      <Header title={copy.savings} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{book?.purpose_text || copy.emptySavings}</Text>
        {book ? <Text style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }}>{vnd(book.amount)}</Text> : null}
      </Card>
    </Screen>
  )
}

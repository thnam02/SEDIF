import { Text } from "react-native"

import { copy } from "../../src/copy/vi"
import { colors, Header, Screen, useType } from "../../src/ui/parts"

export default function BillsScreen() {
  const type = useType()
  return (
    <Screen>
      <Header title={copy.billsTitle} />
      <Text style={{ color: colors.navy, fontSize: type.body }}>{copy.billsBody}</Text>
    </Screen>
  )
}

import { Text } from "react-native"

import { copy } from "../../src/copy/vi"
import { colors, Header, Screen, useType } from "../../src/ui/parts"

export default function TopupScreen() {
  const type = useType()
  return (
    <Screen>
      <Header title={copy.topupTitle} />
      <Text style={{ color: colors.navy, fontSize: type.body }}>{copy.topupBody}</Text>
    </Screen>
  )
}

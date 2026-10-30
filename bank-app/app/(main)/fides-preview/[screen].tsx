import { Text } from "react-native"

import { copy } from "../../../src/copy/vi"
import { colors, Header, Screen } from "../../../src/ui/parts"

export default function FidesPreview() {
  return (
    <Screen>
      <Header title={copy.confirm} />
      <Text style={{ color: colors.navy }}>{copy.level2Body}</Text>
    </Screen>
  )
}

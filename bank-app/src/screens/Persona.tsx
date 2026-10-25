import { Pressable, Text, View } from "react-native"
import { useRouter } from "expo-router"
import { ChevronRight, ShieldCheck } from "lucide-react-native"

import { bank, unwrap } from "../api/client"
import { copy } from "../copy/vi"
import { personas } from "../personas"
import { useBank } from "../store"
import { colors, Screen, useType } from "../ui/parts"

export function PersonaScreen() {
  const router = useRouter()
  const choose = useBank((state) => state.choosePersona)
  const setDesk = useBank((state) => state.setGuardianDesk)
  const type = useType()
  return (
    <Screen>
      <View className="flex-row items-center gap-2" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <ShieldCheck size={25} color={colors.deep} />
        <Text style={{ color: colors.deep, fontWeight: "700", letterSpacing: 2 }}>{copy.logo}</Text>
      </View>
      <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.choose}</Text>
      {personas.map((person) => {
        const label = copy.personas[person.role as keyof typeof copy.personas]
        return (
          <Pressable
            key={person.customer_id}
            accessibilityRole="button"
            onPress={async () => {
              let accountId = person.account_ids[0] ?? ""
              try {
                const rows = await Promise.all(person.account_ids.map((id) => unwrap(bank.GET("/accounts/{id}", { params: { path: { id } } }))))
                const personal = rows.filter((row) => row.account_type === "personal")
                const best = [...(personal.length ? personal : rows)].sort((left, right) => right.balance - left.balance)[0]
                if (best) accountId = best.id
              } catch {
                accountId = person.account_ids[0] ?? ""
              }
              choose(person.customer_id, accountId, person.role)
              router.replace("/(main)/(tabs)")
            }}
            className="flex-row items-center gap-3 rounded-2xl border p-3"
            style={{ flexDirection: "row", alignItems: "center", gap: 12, borderRadius: 16, borderWidth: 1, borderColor: "#F1F5F9", padding: 12 }}
          >
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
              <Text style={{ color: colors.deep, fontWeight: "700" }}>{label?.mark ?? person.full_name.slice(0, 2)}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{label?.title ?? person.full_name}</Text>
              <Text style={{ color: "#64748B", fontSize: type.small }}>{label?.hint ?? copy.bankHint}</Text>
            </View>
            <ChevronRight size={18} color={colors.deep} />
          </Pressable>
        )
      })}
      <Pressable
        accessibilityRole="button"
        onPress={async () => {
          const mother = personas.find((person) => person.has_guardian)
          if (!mother) return
          let accountId = mother.account_ids[0] ?? ""
          try {
            const rows = await Promise.all(mother.account_ids.map((id) => unwrap(bank.GET("/accounts/{id}", { params: { path: { id } } }))))
            const personal = rows.filter((row) => row.account_type === "personal")
            const best = [...(personal.length ? personal : rows)].sort((left, right) => right.balance - left.balance)[0]
            if (best) accountId = best.id
          } catch {
            accountId = mother.account_ids[0] ?? ""
          }
          choose(mother.customer_id, accountId, mother.role)
          setDesk(true)
          router.replace("/(main)/(tabs)/profile")
        }}
        style={{ flexDirection: "row", alignItems: "center", gap: 12, borderRadius: 16, borderWidth: 1, borderColor: "#F1F5F9", padding: 12 }}
      >
        <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.deep, fontWeight: "700" }}>NT</Text>
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{copy.guardianPersona}</Text>
          <Text style={{ color: "#64748B", fontSize: type.small }}>{copy.guardianHint}</Text>
        </View>
        <ChevronRight size={18} color={colors.deep} />
      </Pressable>
    </Screen>
  )
}

import { useState } from "react"
import { Pressable, Text, View } from "react-native"
import { useRouter } from "expo-router"
import { ChevronRight, ShieldCheck } from "lucide-react-native"

import { bank, unwrap } from "../api/client"
import { copy } from "../copy/vi"
import { personas, type Persona } from "../personas"
import { useBank } from "../store"
import { colors, Screen, useType } from "../ui/parts"

export function PersonaScreen() {
  const router = useRouter()
  const choose = useBank((state) => state.choosePersona)
  const setDesk = useBank((state) => state.setGuardianDesk)
  const type = useType()
  const guarded = personas.find((item) => item.has_guardian)
  const guardedHint = `${(guarded?.guardian?.relation ?? "người giám hộ").replaceAll("_", " ")} · ${guarded?.full_name ?? ""}`
  const [error, setError] = useState<string | null>(null)
  async function openPerson(person: Persona, desk: boolean) {
    setError(null)
    try {
      const rows = await Promise.all(person.account_ids.map((id) => unwrap(bank.GET("/accounts/{id}", { params: { path: { id } } }))))
      const personal = rows.filter((row) => row.account_type === "personal")
      const best = [...(personal.length ? personal : rows)].sort((left, right) => right.balance - left.balance)[0]
      if (!best) {
        setError(copy.accountLoadError)
        return
      }
      choose(person.customer_id, best.id, person.role)
      if (desk) setDesk(true)
      router.replace(desk ? "/(main)/(tabs)/profile" : "/(main)/(tabs)")
    } catch {
      setError(copy.accountLoadError)
    }
  }
  return (
    <Screen>
      <View className="flex-row items-center gap-2" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <ShieldCheck size={25} color={colors.deep} />
        <Text style={{ color: colors.deep, fontWeight: "700", letterSpacing: 2 }}>{copy.logo}</Text>
      </View>
      <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700" }}>{copy.choose}</Text>
      {error ? <Text style={{ color: colors.danger }}>{error}</Text> : null}
      {personas.map((person) => {
        const label = copy.personas[person.role as keyof typeof copy.personas]
        return (
          <Pressable
            key={person.customer_id}
            accessibilityRole="button"
            onPress={() => void openPerson(person, false)}
            className="flex-row items-center gap-3 rounded-2xl border p-3"
            style={{ flexDirection: "row", alignItems: "center", gap: 12, borderRadius: 16, borderWidth: 1, borderColor: "#F1F5F9", padding: 12 }}
          >
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
              <Text style={{ color: colors.deep, fontWeight: "700" }}>{label?.mark ?? person.full_name.slice(0, 2)}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{person.full_name}</Text>
              <Text style={{ color: "#64748B", fontSize: type.small }}>{label?.hint ?? copy.bankHint}</Text>
            </View>
            <ChevronRight size={18} color={colors.deep} />
          </Pressable>
        )
      })}
      <Pressable
        accessibilityRole="button"
        onPress={() => {
          if (!guarded) return
          void openPerson(guarded, true)
        }}
        style={{ flexDirection: "row", alignItems: "center", gap: 12, borderRadius: 16, borderWidth: 1, borderColor: "#F1F5F9", padding: 12 }}
      >
        <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.deep, fontWeight: "700" }}>GH</Text>
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.navy, fontWeight: "700", fontSize: type.body }}>{copy.guardianPersona}</Text>
          <Text style={{ color: "#64748B", fontSize: type.small }}>{guardedHint}</Text>
        </View>
        <ChevronRight size={18} color={colors.deep} />
      </Pressable>
    </Screen>
  )
}

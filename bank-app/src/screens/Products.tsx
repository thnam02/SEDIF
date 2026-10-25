import { useState } from "react"
import { Pressable, Text, TextInput, View } from "react-native"
import { useRouter } from "expo-router"
import { Check } from "lucide-react-native"

import { useAccount, useLimit, useProduct, useSavings } from "../api/queries"
import { copy } from "../copy/vi"
import { digits, grouped, vnd } from "../format"
import { displayHolder } from "../names"
import { useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

export function SavingsList() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const books = useSavings(accountId)
  const rows = books.data?.books ?? []
  const type = useType()
  const first = rows[0]
  return (
    <Screen>
      <Header title={copy.savings} />
      {rows.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptySavings}</Text> : null}
      {rows.map((book, index) => (
        <Pressable key={book.id} accessibilityRole="button" onPress={() => router.push(`/(main)/savings/${book.id}`)}>
          <Card>
            <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.book.replace("{n}", String(index + 1).padStart(2, "0"))}</Text>
            <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{vnd(book.balance)}</Text>
            <Text style={{ color: "#64748B", fontSize: type.small }}>{displayHolder(book.holder_name)} · {book.id}</Text>
          </Card>
        </Pressable>
      ))}
      <Button outline label={copy.settle} onPress={() => router.push(first ? `/(main)/savings/${first.id}` : "/(main)/savings/settle")} />
      <Text style={{ color: colors.warn, textAlign: "center", fontSize: type.small }}>{copy.settleNote}</Text>
    </Screen>
  )
}

export function SavingsDetail({ id }: { id: string }) {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const books = useSavings(accountId)
  const book = (books.data?.books ?? []).find((item) => item.id === id)
  const liquidate = useProduct("/savings/liquidate")
  const type = useType()
  return (
    <Screen>
      <Header title={copy.savings} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{book ? displayHolder(book.holder_name) : copy.emptySavings}</Text>
        <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{book ? vnd(book.balance) : ""}</Text>
        <Text style={{ color: "#64748B", fontSize: type.small }}>{book?.id}</Text>
      </Card>
      <Button
        label={copy.settle}
        onPress={() => {
          if (!book) return
          liquidate.mutate(book.balance, { onSuccess: () => router.replace({ pathname: "/(main)/savings/success", params: { amount: String(book.balance) } }) })
        }}
      />
      <Text style={{ color: colors.warn, textAlign: "center", fontSize: type.small }}>{copy.settleNote}</Text>
    </Screen>
  )
}

export function SavingsSettle() {
  const router = useRouter()
  const [amount, setAmount] = useState("200000000")
  const liquidate = useProduct("/savings/liquidate")
  return (
    <Screen>
      <Header title={copy.settle} />
      <Card>
        <Text style={{ color: "#64748B" }}>{copy.moneyLabel}</Text>
        <TextInput value={grouped(Number(amount) || 0)} onChangeText={(value) => setAmount(digits(value))} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
      </Card>
      <Text style={{ color: colors.warn, textAlign: "center" }}>{copy.settleNote}</Text>
      <Button
        label={copy.settle}
        onPress={() => liquidate.mutate(Number(amount) || 0, { onSuccess: () => router.replace({ pathname: "/(main)/savings/success", params: { amount } }) })}
      />
    </Screen>
  )
}

export function SavingsSuccess({ amount }: { amount: string }) {
  const router = useRouter()
  const value = Number(amount) || 0
  const type = useType()
  return (
    <Screen>
      <View style={{ alignItems: "center", gap: 20, paddingVertical: 40 }}>
        <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
          <Check size={36} color={colors.deep} />
        </View>
        <Text style={{ color: colors.navy, fontSize: type.title, fontWeight: "700", textAlign: "center" }}>{copy.settledLive.replace("{amount}", vnd(value))}</Text>
        <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
      </View>
    </Screen>
  )
}

export function LoanScreen() {
  const router = useRouter()
  const [amount, setAmount] = useState("10000000")
  const loan = useProduct("/loan")
  const type = useType()
  return (
    <Screen>
      <Header title={copy.loan} />
      <Card>
        <Text style={{ color: "#64748B", fontSize: type.body }}>{copy.moneyLabel}</Text>
        <TextInput value={grouped(Number(amount) || 0)} onChangeText={(value) => setAmount(digits(value))} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
        <Text style={{ color: "#64748B", fontSize: type.body }}>{copy.disburse}</Text>
      </Card>
      <Button label={copy.loan} onPress={() => loan.mutate(Number(amount) || 0, { onSuccess: () => router.push({ pathname: "/(main)/loans/success", params: { amount } }) })} />
    </Screen>
  )
}

export function LoanSuccess({ amount }: { amount: string }) {
  const router = useRouter()
  const type = useType()
  return (
    <Screen>
      <View style={{ alignItems: "center", gap: 20, paddingVertical: 40 }}>
        <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: colors.aqua, alignItems: "center", justifyContent: "center" }}>
          <Check size={36} color={colors.deep} />
        </View>
        <Text style={{ color: "#2E7D32", fontSize: type.title, fontWeight: "700", textAlign: "center" }}>{copy.disbursedLive.replace("{amount}", vnd(Number(amount) || 0))}</Text>
        <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
      </View>
    </Screen>
  )
}

export function LimitScreen() {
  const accountId = useBank((state) => state.accountId)
  const account = useAccount(accountId)
  const [perTxn, setPerTxn] = useState("")
  const [perDay, setPerDay] = useState("")
  const limit = useLimit()
  const txn = perTxn || String(account.data?.limit_per_txn ?? "")
  const dayLimit = perDay || String(account.data?.limit_per_day ?? "")
  return (
    <Screen>
      <Header title={copy.limits} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.limitPer}</Text>
        <TextInput testID="limit-txn" value={grouped(Number(txn) || 0)} onChangeText={(value) => setPerTxn(digits(value))} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
        <Text style={{ color: "#64748B" }}>{copy.limitDay}</Text>
        <TextInput value={grouped(Number(dayLimit) || 0)} onChangeText={(value) => setPerDay(digits(value))} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
      </Card>
      <Button label={limit.isSuccess ? copy.updated : copy.update} onPress={() => limit.mutate({ limit_per_txn: Number(txn) || 0, limit_per_day: Number(dayLimit) || 0 })} />
    </Screen>
  )
}

export function CardScreen() {
  const accountId = useBank((state) => state.accountId)
  const account = useAccount(accountId)
  const [amount, setAmount] = useState("1000000")
  const cash = useProduct("/card/cash-advance")
  return (
    <Screen>
      <Header title={copy.cash} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.available}</Text>
        <Text style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }}>{vnd(account.data?.balance ?? 0)}</Text>
        <Text style={{ color: "#64748B" }}>{copy.fee}</Text>
        <TextInput value={grouped(Number(amount) || 0)} onChangeText={(value) => setAmount(digits(value))} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
      </Card>
      <Button label={copy.withdraw} onPress={() => cash.mutate(Number(amount) || 0)} />
    </Screen>
  )
}

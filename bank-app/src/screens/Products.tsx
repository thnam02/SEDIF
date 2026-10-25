import { useState } from "react"
import { Text, TextInput } from "react-native"
import { useRouter } from "expo-router"

import { useAccount, useHistory, useLimit, useProduct } from "../api/queries"
import { copy } from "../copy/vi"
import { vnd } from "../format"
import { useBank } from "../store"
import { Button, Card, colors, Header, Screen, useType } from "../ui/parts"

export function SavingsList() {
  const router = useRouter()
  const accountId = useBank((state) => state.accountId)
  const history = useHistory(accountId)
  const books = (history.data?.entries ?? []).filter((item) => item.tx_type === "savings_liquidation")
  const type = useType()
  return (
    <Screen>
      <Header title={copy.savings} />
      {books.length === 0 ? <Text style={{ color: "#64748B" }}>{copy.emptySavings}</Text> : null}
      {books.map((book) => (
        <Card key={book.tx_id}>
          <Text style={{ color: colors.navy, fontWeight: "700" }}>{book.purpose_text || book.tx_type}</Text>
          <Text style={{ color: colors.deep, fontSize: type.title, fontWeight: "700" }}>{vnd(book.amount)}</Text>
        </Card>
      ))}
      <Button outline label={copy.settle} onPress={() => router.push("/(main)/savings/settle")} />
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
        <TextInput value={amount} onChangeText={setAmount} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
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
  return (
    <Screen>
      <Header title={copy.savings} />
      <Card>
        <Text style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }}>{copy.settledLive.replace("{amount}", vnd(value))}</Text>
      </Card>
      <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
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
        <TextInput value={amount} onChangeText={setAmount} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 30, fontWeight: "700" }} />
        <Text style={{ color: "#64748B", fontSize: type.body }}>{copy.disburse}</Text>
      </Card>
      <Button label={copy.loan} onPress={() => loan.mutate(Number(amount) || 0, { onSuccess: () => router.push({ pathname: "/(main)/loans/success", params: { amount } }) })} />
    </Screen>
  )
}

export function LoanSuccess({ amount }: { amount: string }) {
  const router = useRouter()
  return (
    <Screen>
      <Header title={copy.loan} />
      <Text style={{ color: "#2E7D32", textAlign: "center", fontWeight: "700" }}>{copy.disbursedLive.replace("{amount}", vnd(Number(amount) || 0))}</Text>
      <Button label={copy.homeButton} onPress={() => router.replace("/(main)/(tabs)")} />
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
  const day = perDay || String(account.data?.limit_per_day ?? "")
  return (
    <Screen>
      <Header title={copy.limits} />
      <Card>
        <Text style={{ color: colors.navy, fontWeight: "700" }}>{copy.limitPer}</Text>
        <TextInput value={txn} onChangeText={setPerTxn} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
        <Text style={{ color: "#64748B" }}>{copy.limitDay}</Text>
        <TextInput value={day} onChangeText={setPerDay} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
      </Card>
      <Button label={copy.update} onPress={() => limit.mutate({ limit_per_txn: Number(txn) || 0, limit_per_day: Number(day) || 0 })} />
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
        <TextInput value={amount} onChangeText={setAmount} keyboardType="number-pad" style={{ color: colors.deep, fontSize: 24, fontWeight: "700" }} />
      </Card>
      <Button label={copy.withdraw} onPress={() => cash.mutate(Number(amount) || 0)} />
    </Screen>
  )
}

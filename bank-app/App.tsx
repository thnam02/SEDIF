import { useState } from "react"
import { Pressable, Text, TextInput, View } from "react-native"

import { CallPermissionScreen, FidesSession, useAccountInput } from "fides-sdk"

const BANK = process.env.EXPO_PUBLIC_BANK_URL ?? "http://127.0.0.1:8092"
const FROM = "A0002119"
const PAYEES = [
  { id: "A0099001", name: "Người nhận đã lưu" },
]

export default function App() {
  const [ready, setReady] = useState(false)
  const [hiddenTaps, setHiddenTaps] = useState(0)
  const [simulated, setSimulated] = useState(false)
  const [amount, setAmount] = useState("1000000")
  const [result, setResult] = useState("")
  const input = useAccountInput()

  if (!ready) {
    return <CallPermissionScreen onContinue={() => { FidesSession.start(); setReady(true) }} />
  }

  function toggleHidden() {
    const next = hiddenTaps + 1
    setHiddenTaps(next)
    if (next >= 7) {
      const active = !simulated
      setSimulated(active)
      FidesSession.setSimulatedCall(active, active)
      setHiddenTaps(0)
    }
  }

  async function submit() {
    const device = FidesSession.collectSignals()
    const response = await fetch(`${BANK}/transfer`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `${FROM}-${input.value}-${amount}`,
      },
      body: JSON.stringify({
        from_account: FROM,
        to_account: input.value,
        amount: Number(amount),
        device,
      }),
    })
    const payload = await response.json()
    setResult(`${response.status} ${payload.status ?? payload.detail ?? ""}`)
  }

  return (
    <View>
      <Pressable onPress={toggleHidden}>
        <Text>Chuyển tiền{simulated ? " · giả lập cuộc gọi" : ""}</Text>
      </Pressable>
      <TextInput value={amount} onChangeText={setAmount} keyboardType="number-pad" placeholder="Số tiền" />
      <TextInput value={input.value} onChangeText={input.onChangeText} placeholder="Số tài khoản" />
      {PAYEES.map((payee) => (
        <Pressable key={payee.id} onPress={() => input.choose(payee.id)}>
          <Text>{payee.name}</Text>
        </Pressable>
      ))}
      <Pressable onPress={submit}>
        <Text>Gửi lệnh</Text>
      </Pressable>
      <Text>{result}</Text>
    </View>
  )
}

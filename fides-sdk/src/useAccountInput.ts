import { useRef, useState } from "react"

import { classifyAccountInput, type AccountInput, type InputEvent } from "./accountInput"
import { FidesSession } from "./FidesSession"
import { sessionRecorder } from "./session/SessionRecorder"

export function useAccountInput() {
  const events = useRef<InputEvent[]>([])
  const [value, setValue] = useState("")
  const [acctInput, setAcctInput] = useState<AccountInput | null>(null)

  function remember(event: InputEvent) {
    events.current = [...events.current, event]
    setAcctInput(classifyAccountInput(events.current))
    FidesSession.noteInput(event)
  }

  function onChangeText(next: string) {
    const now = Date.now()
    if (next.length < value.length) {
      sessionRecorder.note({ type: "field_edit", t: now })
    } else if (next.length - value.length > 1) {
      remember({ type: "paste", t: now })
      sessionRecorder.note({ type: "paste", t: now, payee: true })
    } else if (next.length > value.length) {
      remember({ type: "key", t: now })
      sessionRecorder.note({ type: "key_tick", t: now, payee: true })
    }
    setValue(next)
  }

  function choose(account: string) {
    remember({ type: "choose", t: Date.now() })
    sessionRecorder.note({ type: "saved_payee_pick" })
    setValue(account)
  }

  return { value, onChangeText, choose, acctInput }
}

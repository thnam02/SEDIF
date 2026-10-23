export type AccountInput = "typed" | "typed_chunked" | "pasted" | "chosen"

export type InputEvent = { type: "key" | "paste" | "choose"; t: number }

const CHUNK_GAP_MS = 1500

/** Gõ đều, gõ cụm khi nghỉ hơn 1,5s, dán một lần, hoặc chọn từ danh bạ thụ hưởng. */
export function classifyAccountInput(events: InputEvent[]): AccountInput | null {
  if (events.some((event) => event.type === "choose")) {
    return "chosen"
  }
  const keys = events.filter((event) => event.type === "key").sort((left, right) => left.t - right.t)
  const pastes = events.filter((event) => event.type === "paste")
  if (pastes.length > 0 && keys.length === 0) {
    return "pasted"
  }
  if (keys.length === 0) {
    return null
  }
  for (let index = 1; index < keys.length; index += 1) {
    if (keys[index].t - keys[index - 1].t > CHUNK_GAP_MS) {
      return "typed_chunked"
    }
  }
  return "typed"
}

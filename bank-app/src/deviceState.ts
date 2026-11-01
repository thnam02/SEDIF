/** none = máy báo không ghi. unknown = không biết, engine không coi là vắng. */
export type DeviceState = "active_capture" | "none" | "unknown"

export function deviceState(captured: boolean | null, demoForcesCapture: boolean): DeviceState {
  if (demoForcesCapture) return "active_capture"
  if (captured === true) return "active_capture"
  if (captured === false) return "none"
  return "unknown"
}

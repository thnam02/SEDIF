import type { BehaviourFeatures, CallBlock, TaskType } from "fides-sdk"

export type LastSession = {
  task_type: TaskType
  call: CallBlock
  behaviour_features: BehaviourFeatures
}

let last: LastSession | null = null

export function rememberSession(value: LastSession) {
  last = value
}

export function readLastSession(): LastSession | null {
  return last
}

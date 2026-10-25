import { create } from "zustand"

import type { components } from "./api/openapi"

type Outcome = {
  transferId: string
  status: components["schemas"]["TransferStatus"]
  level: components["schemas"]["BankDecision"]["level"]
  actions: string[]
  reasons: components["schemas"]["Reason"][]
  funds_status: "held" | "released" | "cancelled"
  gate: "none" | "guardian_confirm_requested" | "short_wait"
  hold_until: string | null
  countdown_until: string | null
  payee: string
  payeeName: string
  amount: number
}

type BankState = {
  customerId: string | null
  accountId: string | null
  role: string | null
  largeText: boolean
  strangerCall: boolean
  toAccount: string
  toName: string
  amount: number
  note: string
  outcome: Outcome | null
  choosePersona: (customerId: string, accountId: string, role: string) => void
  setLargeText: (value: boolean) => void
  setStrangerCall: (value: boolean) => void
  setPayee: (account: string, name: string) => void
  setAmount: (amount: number) => void
  setNote: (note: string) => void
  setOutcome: (outcome: Outcome | null) => void
}

export const useBank = create<BankState>((set) => ({
  customerId: null,
  accountId: null,
  role: null,
  largeText: false,
  strangerCall: false,
  toAccount: "",
  toName: "",
  amount: 500_000,
  note: "",
  outcome: null,
  choosePersona: (customerId, accountId, role) => set({ customerId, accountId, role, outcome: null }),
  setLargeText: (largeText) => set({ largeText }),
  setStrangerCall: (strangerCall) => set({ strangerCall }),
  setPayee: (toAccount, toName) => set({ toAccount, toName }),
  setAmount: (amount) => set({ amount }),
  setNote: (note) => set({ note }),
  setOutcome: (outcome) => set({ outcome }),
}))

export const DEMO = process.env.EXPO_PUBLIC_DEMO_MODE === "1"

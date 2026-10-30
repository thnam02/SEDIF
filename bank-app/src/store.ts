import { create } from "zustand"

import type { components } from "./api/openapi"

type Outcome = {
  transferId: string
  decisionId: string
  status: components["schemas"]["TransferStatus"]
  level: components["schemas"]["BankDecision"]["level"]
  engineLevel: 0 | 1 | 2 | 3
  intervention: components["schemas"]["Intervention"]
  actions: string[]
  reasons: components["schemas"]["Reason"][]
  reasonsVi: string[]
  familiesHit: string[]
  funds_status: "held" | "released" | "cancelled"
  gate: "none" | "guardian_confirm_requested" | "short_wait"
  hold_until: string | null
  countdown_until: string | null
  payee: string
  payeeName: string
  amount: number
  experiment?: boolean
}

export type EntryDetail = "paste" | "image_capture" | "image_upload"
export type TransferPurpose =
  | "authority_request"
  | "fee_to_receive"
  | "investment"
  | "refund_request"
  | "purchase"
  | "family_friends"
  | "debt_salary"
  | "other"

type BankState = {
  customerId: string | null
  accountId: string | null
  role: string | null
  largeText: boolean
  strangerCall: boolean
  guardianDesk: boolean
  devCapture: boolean
  devGuided: boolean
  toAccount: string
  toName: string
  amount: number
  note: string
  purpose: TransferPurpose | null
  entryDetail: EntryDetail | null
  outcome: Outcome | null
  choosePersona: (customerId: string, accountId: string, role: string) => void
  setAccount: (accountId: string) => void
  setLargeText: (value: boolean) => void
  setStrangerCall: (value: boolean) => void
  setGuardianDesk: (value: boolean) => void
  setDevCapture: (value: boolean) => void
  setDevGuided: (value: boolean) => void
  setPayee: (account: string, name: string) => void
  setAmount: (amount: number) => void
  setNote: (note: string) => void
  setPurpose: (purpose: TransferPurpose | null) => void
  setEntryDetail: (entryDetail: EntryDetail | null) => void
  setOutcome: (outcome: Outcome | null) => void
}

export const useBank = create<BankState>((set) => ({
  customerId: null,
  accountId: null,
  role: null,
  largeText: false,
  strangerCall: false,
  guardianDesk: false,
  devCapture: false,
  devGuided: false,
  toAccount: "",
  toName: "",
  amount: 500_000,
  note: "",
  purpose: null,
  entryDetail: null,
  outcome: null,
  choosePersona: (customerId, accountId, role) => set({ customerId, accountId, role, outcome: null, guardianDesk: false, devCapture: false, devGuided: false, purpose: null, entryDetail: null, note: "" }),
  setAccount: (accountId) => set({ accountId }),
  setLargeText: (largeText) => set({ largeText }),
  setStrangerCall: (strangerCall) => set({ strangerCall }),
  setGuardianDesk: (guardianDesk) => set({ guardianDesk }),
  setDevCapture: (devCapture) => set({ devCapture }),
  setDevGuided: (devGuided) => set({ devGuided }),
  setPayee: (toAccount, toName) => set((state) => ({ toAccount, toName, purpose: state.toAccount === toAccount ? state.purpose : null })),
  setAmount: (amount) => set({ amount }),
  setNote: (note) => set({ note }),
  setPurpose: (purpose) => set({ purpose }),
  setEntryDetail: (entryDetail) => set({ entryDetail }),
  setOutcome: (outcome) => set({ outcome }),
}))

export const DEMO = process.env.EXPO_PUBLIC_DEMO_MODE === "1"

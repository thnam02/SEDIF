import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { FidesSession } from "fides-sdk"

import { bank, unwrap } from "./client"
import { transferKey } from "./idempotency"
import type { components } from "./openapi"
import { useBank } from "../store"

export function useAccount(id: string | null) {
  return useQuery({
    queryKey: ["account", id],
    enabled: Boolean(id),
    queryFn: () => unwrap(bank.GET("/accounts/{id}", { params: { path: { id: id ?? "" } } })),
  })
}

export function useHistory(id: string | null) {
  return useQuery({
    queryKey: ["history", id],
    enabled: Boolean(id),
    refetchInterval: 4000,
    queryFn: () => unwrap(bank.GET("/accounts/{id}/history", { params: { path: { id: id ?? "" } } })),
  })
}

export function useBeneficiaries(id: string | null) {
  return useQuery({
    queryKey: ["beneficiaries", id],
    enabled: Boolean(id),
    queryFn: () => unwrap(bank.GET("/accounts/{id}/beneficiaries", { params: { path: { id: id ?? "" } } })),
  })
}

export function useGuardianRequests() {
  return useQuery({
    queryKey: ["guardian-requests"],
    refetchInterval: 4000,
    queryFn: () => unwrap(bank.GET("/guardian/requests")),
  })
}

function refreshAccount(client: ReturnType<typeof useQueryClient>, id: string) {
  return Promise.all([
    client.invalidateQueries({ queryKey: ["account", id] }),
    client.invalidateQueries({ queryKey: ["history", id] }),
  ])
}

export function useProduct(path: "/savings/liquidate" | "/loan" | "/card/cash-advance") {
  const accountId = useBank((state) => state.accountId) ?? ""
  const client = useQueryClient()
  return useMutation({
    mutationFn: (amount: number) =>
      unwrap(bank.POST(path, { body: { account_id: accountId, amount, device: FidesSession.collectSignals() } })),
    onSuccess: () => refreshAccount(client, accountId),
  })
}

export function useLimit() {
  const accountId = useBank((state) => state.accountId) ?? ""
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { limit_per_txn: number; limit_per_day: number }) =>
      unwrap(bank.POST("/limit", { body: { account_id: accountId, ...body, device: FidesSession.collectSignals() } })),
    onSuccess: () => refreshAccount(client, accountId),
  })
}

export function useTransfer() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: { from: string; to: string; amount: number; note: string }) => {
      const key = await transferKey(input.from, input.to, input.amount)
      return unwrap(
        bank.POST("/transfer", {
          params: { header: { "Idempotency-Key": key } },
          body: {
            from_account: input.from,
            to_account: input.to,
            amount: input.amount,
            purpose_text: input.note || undefined,
            device: FidesSession.collectSignals(),
          },
        }),
      )
    },
    onSuccess: (_data, input) => refreshAccount(client, input.from),
  })
}

export function useRespond() {
  return useMutation({
    mutationFn: (input: { id: string; body: components["schemas"]["TransferRespondRequest"] }) =>
      unwrap(bank.POST("/transfer/{id}/respond", { params: { path: { id: input.id } }, body: input.body })),
  })
}

export function useConfirmGuardian() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => unwrap(bank.POST("/guardian/requests/{id}/confirm", { params: { path: { id } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: ["guardian-requests"] }),
  })
}

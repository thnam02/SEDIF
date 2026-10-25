import raw from "../../docs/personas.json"

export type Persona = {
  customer_id: string
  full_name: string
  role: string
  segment: string
  has_guardian: boolean
  account_ids: string[]
  guardian?: { relation?: string }
}

export const personas = raw.personas as Persona[]

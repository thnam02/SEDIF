import type { EntryDetail } from "./store"

export type AddMethod = "typed" | "paste" | "qr" | "contact"
export type TransferTask = "saved_payee" | "new_typed" | "new_pasted" | "qr"

/** Model-v2.1 chỉ có bốn cách nhập. Ảnh tới một lần, cùng nhóm với dán. */
export function addMethod(task: TransferTask, entry?: EntryDetail | null): AddMethod {
  if (task === "qr") return "qr"
  if (task === "saved_payee") return "contact"
  if (entry === "paste" || entry === "image_capture" || entry === "image_upload") return "paste"
  if (task === "new_pasted") return "paste"
  return "typed"
}

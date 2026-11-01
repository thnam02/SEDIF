export const LEVEL1_EXPIRED = "Lệnh đã hết hạn, vui lòng thực hiện lại"

export type Level1Leave = "navigation" | "background" | "unmount" | "confirm"

/** Control Center, banner cuộc gọi đến, và thanh cuộc gọi xanh là inactive. Chỉ background mới huỷ. */
export function level1AppStateCancels(state: string): boolean {
  return state === "background"
}

/** Rời bảng nhắc thì huỷ. Bấm tiếp tục thì giữ lệnh. */
export function level1OnLeave(event: Level1Leave): { request: "cancel" | "none"; forgetKey: boolean } {
  if (event === "confirm") return { request: "none", forgetKey: false }
  return { request: "cancel", forgetKey: true }
}

/** 409 của confirm: hiện đúng câu ngân hàng trả và quay lại form. */
export function level1ConfirmRejected(status: number, detail: string): { back: boolean; message: string; forgetKey: boolean } | null {
  if (status !== 409) return null
  return { back: true, message: detail, forgetKey: true }
}

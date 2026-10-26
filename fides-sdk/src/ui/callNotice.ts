/** GIẢ ĐỊNH: tối đa `max` lần trong cửa sổ, trừ khi đang chạy demo. */

export function showCallNotice(inCall: boolean, used: number, max: number, demo: boolean): boolean {
  if (!inCall) return false
  if (demo) return true
  return used < max
}

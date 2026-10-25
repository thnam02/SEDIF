import { useEffect } from "react"
import { AppState, PermissionsAndroid, Platform } from "react-native"

import { FidesSession } from "./FidesSession"
import { FidesPermissionScreen } from "./ui"

export function CallPermissionScreen({ onContinue }: { onContinue: () => void }) {
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => {
      if (next === "background") {
        FidesSession.noteAppSwitch()
      }
    })
    return () => subscription.remove()
  }, [])

  async function allow() {
    if (Platform.OS === "android") {
      await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE, {
        title: "Cuộc gọi đang mở",
        message: "FIDES chỉ cần biết máy đang trong cuộc gọi hay không. Ứng dụng không đọc số điện thoại, lịch sử cuộc gọi, tin nhắn hay danh bạ. Từ chối vẫn chuyển tiền được; cuộc gọi VoIP vẫn được nhận qua chế độ âm thanh.",
        buttonPositive: "Cho phép",
        buttonNegative: "Để sau",
      })
    }
    onContinue()
  }

  return <FidesPermissionScreen onAllow={allow} onLater={onContinue} />
}

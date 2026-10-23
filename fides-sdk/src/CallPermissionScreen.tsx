import { useEffect } from "react"
import { AppState, PermissionsAndroid, Platform, Pressable, Text, View } from "react-native"

import { FidesSession } from "./FidesSession"

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

  return (
    <View>
      <Text>Khi chuyển tiền, FIDES nhìn cuộc gọi đang mở để cảnh báo kịp lúc.</Text>
      <Text>Không đọc số máy, nhật ký cuộc gọi, SMS hay danh bạ. Từ chối quyền thì vẫn dùng được app.</Text>
      <Pressable onPress={allow}>
        <Text>Cho phép trạng thái cuộc gọi</Text>
      </Pressable>
      <Pressable onPress={onContinue}>
        <Text>Tiếp tục không cấp quyền</Text>
      </Pressable>
    </View>
  )
}

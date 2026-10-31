import { useSyncExternalStore } from "react"
import { Modal, Pressable, Text, View } from "react-native"
import { FidesSession } from "fides-sdk"

import { DEMO, useBank } from "../store"
import { colors } from "../ui/parts"

let open = false
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

export function openDemoDrawer() {
  open = true
  emit()
}

export function closeDemoDrawer() {
  open = false
  emit()
}

function useDemoOpen() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => open,
    () => false,
  )
}

export function DemoControls() {
  const devCapture = useBank((state) => state.devCapture)
  const setDevCapture = useBank((state) => state.setDevCapture)
  const devGuided = useBank((state) => state.devGuided)
  const setDevGuided = useBank((state) => state.setDevGuided)
  const setStrangerCall = useBank((state) => state.setStrangerCall)
  return (
    <View style={{ gap: 8 }}>
      <Pressable testID="dev-active-capture" accessibilityRole="button" onPress={() => { setDevCapture(!devCapture); closeDemoDrawer() }} style={{ minHeight: 44, justifyContent: "center" }}>
        <Text style={{ color: devCapture ? colors.warn : colors.deep, fontWeight: "700" }}>Demo: màn hình đang bị ghi</Text>
      </Pressable>
      <Pressable testID="dev-guided" accessibilityRole="button" onPress={() => { setDevGuided(!devGuided); closeDemoDrawer() }} style={{ minHeight: 44, justifyContent: "center" }}>
        <Text style={{ color: devGuided ? colors.warn : colors.deep, fontWeight: "700" }}>Demo: phiên có hướng dẫn</Text>
      </Pressable>
      <Pressable testID="call-end" accessibilityRole="button" onPress={() => { FidesSession.setSimulatedCall(false, false); setStrangerCall(false); closeDemoDrawer() }} style={{ minHeight: 44, justifyContent: "center" }}>
        <Text style={{ color: colors.deep, fontWeight: "700" }}>Demo: kết thúc cuộc gọi</Text>
      </Pressable>
    </View>
  )
}

export function DemoDrawer() {
  const shown = useDemoOpen()
  if (!DEMO || !shown) return null
  return (
    <Modal transparent animationType="slide" onRequestClose={closeDemoDrawer}>
      <Pressable accessibilityRole="button" onPress={closeDemoDrawer} style={{ flex: 1, backgroundColor: "rgba(11,42,74,0.4)" }} />
      <View style={{ backgroundColor: colors.white, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 20, gap: 12 }}>
        <Text style={{ color: colors.navy, fontWeight: "700", fontSize: 18 }}>Phiên FIDES</Text>
        <DemoControls />
      </View>
    </Modal>
  )
}

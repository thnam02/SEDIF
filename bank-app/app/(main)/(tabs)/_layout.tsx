import { Pressable, View } from "react-native"
import { Tabs } from "expo-router"
import { BarChart3, Home, QrCode, UserRound, Wallet } from "lucide-react-native"

import { copy } from "../../../src/copy/vi"
import { colors } from "../../../src/ui/parts"

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: colors.deep }}>
      <Tabs.Screen name="index" options={{ title: copy.nav[0], tabBarIcon: ({ color }) => <Home color={color} size={18} /> }} />
      <Tabs.Screen name="stats" options={{ title: copy.nav[1], tabBarIcon: ({ color }) => <BarChart3 color={color} size={18} /> }} />
      <Tabs.Screen
        name="scan"
        options={{
          title: "",
          tabBarIcon: () => (
            <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: colors.deep, alignItems: "center", justifyContent: "center", marginBottom: 24 }}>
              <QrCode color={colors.white} size={26} />
            </View>
          ),
          tabBarButton: (props) => (
            <Pressable testID="tab-qr" accessibilityRole="button" onPress={props.onPress} onLongPress={props.onLongPress} style={props.style}>
              {props.children}
            </Pressable>
          ),
        }}
      />
      <Tabs.Screen name="loans" options={{ title: copy.nav[2], tabBarIcon: ({ color }) => <Wallet color={color} size={18} /> }} />
      <Tabs.Screen name="profile" options={{ title: copy.nav[3], tabBarIcon: ({ color }) => <UserRound color={color} size={18} /> }} />
    </Tabs>
  )
}

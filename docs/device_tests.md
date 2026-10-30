# Kiểm tra cuộc gọi trên Android thật

Chưa đo trên máy. Mỗi dòng điền `in_call`, `voip_suspected`, `call_ended_in_session` sau khi mở màn chuyển tiền rồi thực hiện tình huống. `call_direction` trên Android luôn là null. Từ chối quyền thì `in_call` phải là null; VoIP không cần quyền.

| Tình huống | in_call | voip_suspected | call_ended_in_session |
| --- | --- | --- | --- |
| Cuộc gọi di động đến |  |  |  |
| Cuộc gọi di động đi |  |  |  |
| Zalo gọi thoại |  |  |  |
| Zalo gọi video |  |  |  |
| Messenger |  |  |  |
| WhatsApp |  |  |  |
| Zoom |  |  |  |
| Từ chối quyền READ_PHONE_STATE |  |  |  |
| Gọi rồi cúp giữa phiên |  |  |  |

## iPhone

- Root cause, v2.2 `FidesIntervention` buttons dead during a cellular call: iOS grows the status-bar frame for the in-call bar. `Screen` in `bank-app/src/ui/parts.tsx` padded by `useSafeAreaInsets().top` and put the buttons in a `ScrollView` whose default `contentInsetAdjustmentBehavior` is `automatic`, so UIKit applied that bar a second time. `SafeAreaProvider` in `app/_layout.tsx` does not refresh insets on `statusBarFrame` changes. The painted `Pressable` and its hit target then differed by the bar height, and a tap on the medium guide rows or the high-screen buttons landed on empty scroll space. Those screens are removed. The scroll view now uses `contentInsetAdjustmentBehavior="never"` so the inset is applied once.
- Cellular call on iPhone: detected (call pill shown).
- The button retest during a live cellular call was not run. The phone was paired, and no call was on screen, so the in-call status bar was not exercised.

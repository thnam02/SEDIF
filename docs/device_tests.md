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

- TODO: FidesIntervention medium screen buttons not responding on iPhone during active call — fix in step 6 when replacing with three-button screen.
- Cellular call on iPhone: detected (call pill shown).

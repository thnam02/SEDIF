# Hợp đồng API v2.1

Gói `fides/out/model-v2.0`, không train lại. `POST /decide` tính feature tại `ts` từ sổ quan sát. Thiếu `device` thì profile bị hạ xuống `basic`.

`POST /respond`: `who_guides_answer` chỉ được nâng `none < banner < medium < high`. `guardian_confirm=true` mới cho khách có giám hộ đi tiếp khi mức là Medium. `cancel=true` trong 24 giờ kể từ lúc ra quyết định thì trạng thái `cancelled`.

Audit: mỗi quyết định một dòng JSONL. Chỉ lưu hash SHA-256 của số tài khoản.

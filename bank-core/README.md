# bank-core

Lõi ngân hàng giả. Ảnh hiện tại phục vụ `GET /health` và dùng chung `engine/common/hashing.py`. Thiếu `FIDES_HMAC_KEY` thì không khởi động. Hợp đồng nằm ở `docs/openapi.yaml`.

## Chạy demo

Khởi động không nạp lại sổ. `BANK_RESEED` mặc định là `0`. Đặt `BANK_RESEED=1` chỉ nạp lại phía ngân hàng và làm lệch `reset_id` với engine.

Muốn làm sạch dữ liệu thì chỉ dùng demo_reset.sh. Script đó đặt lại cả bank-core, events/decisions/responses sau 20/9, nạp lại RDB Redis và đồng hồ, cùng một `reset_id` cho hai phía. `GET /health` của bank-core và engine trả `reset_id`. Khác nhau thì engine trả 409 `ledger_mismatch`.

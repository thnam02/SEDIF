# Hợp đồng API v2.1

Nguồn chi tiết là `docs/openapi.yaml`. Engine chưa được viết lại theo bản này.

App không gửi `ts`. Bank-core đóng dấu đồng hồ demo, bắt đầu 2026-09-20, nhanh gấp 60, phủ đến trước 2026-09-29.

Hash gửi engine là HMAC-SHA256, khoá `FIDES_HMAC_KEY`.

`device` trên `/decide` là optional. Mọi trường nullable. `acct_input` là `typed`, `typed_chunked`, `pasted`, `chosen`. `call_direction` null trên Android.

`who_guides` là `authority`, `bank`, `relative`, `friend`, `seller`, `nobody`, `other`. `authority` và `bank` đưa mức lên high. `relative` với giám hộ đặt `guardian_confirm_requested`. `relative` không giám hộ là chờ ngắn. Mức không bao giờ hạ. `hung_up` ở high bắt đầu countdown 10 phút, reset nếu có cuộc gọi mới, và không huỷ. Tiền high giữ 24 giờ. `proceed` sau countdown thì `released`. `cancel` bất cứ lúc nào thì `cancelled`. `guardian_confirm` nhả sớm. `short_wait` là 2 phút. Medium giữ 30 phút. Các mốc là GIẢ ĐỊNH trong `fides-sim/config.yaml`.

`/savings/liquidate`, `/loan`, `/card/cash-advance`, `/limit` chỉ ghi `/events` và trả `done`.

`/transfer/{id}/respond` không nhận `decision_id`.

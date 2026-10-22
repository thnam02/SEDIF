# FIDES model v2.1

Seed 42. Cùng split: train trước ngày 50, nhãn là báo cáo đã tới hạn; test từ 2026-08-30, nhãn `is_fraud`. Hiệu chỉnh isotonic out-of-fold theo vụ, 5 fold, rồi dịch odds. Ngưỡng τ(A) = c_FP(A) / (c_FP(A) + η·A), η = 1. c_FP doanh nghiệp nhỏ ×3. τ_hi là mốc thấp nhất còn giữ Medium+High trên phiên hợp lệ ≤ 0,45%. CI 95% bootstrap theo vụ, 200 lần, seed 42. Hiệu số giữa các profile dùng cùng một lần resample.

Ba profile được đóng gói ở `engine/artifacts/model-v2.1/<profile>/`: `model.txt`, `policy.json`, `features.json`, `oof.npy`, `SHA256SUMS`. Bản advanced+telco+PAD chỉ có trong bảng, không có thư mục artifact.

Đối chiếu với model v2.0 đã đóng gói (27 cột, không T4c, không g3 trong mức): tiền tránh được 25,4% của 12.539.478.000 VND, recall@1% 56,6%. Bản telco+PAD v2.1: 24,13% và 57,74%.

## Cột

**basic, 17 cột.** Sổ: `amount`, `amount_to_balance`, `a_w`, `new_payee`, `add_method`, `hour`, `seconds_since_login`, `new_payees_24h`, `has_tx_signals`. Tra cứu tên và danh sách tổ chức: `payee_name_matches_holder`, `merchant_verified`, `has_payee_signals`. Sản phẩm 7 ngày: `product_savings_7d`, `product_loan_7d`, `product_cash_advance_7d`. Thay đổi tài khoản: `limit_raise_1d`, `phone_change_7d`.

**standard, 24 cột.** Basic cộng SDK: `in_call`, `call_seconds_since_open`, `call_ended_in_session`, `acct_input` (`typed`, `typed_chunked`, `pasted`, `chosen`), `app_switches`, cộng `pasted` và `has_device_signals`.

**advanced, 33 cột.** Standard cộng `payee_account_age_days`, `distinct_senders_7d`, `passthrough_24h`, `device_holder_count`, bốn cờ `*_missing`, và `simo_listed`.

**advanced+telco+PAD, 45 cột.** Advanced cộng telco (`on_call`, `recent_call_minutes_ago`, `counterparty_is_new`, `counterparty_number_age_days`, `counterparty_report_count`, `has_telco_signals`), PAD (`accessibility_new`, `remote_access_app`, `screen_sharing`, `overlay`), và hai tín hiệu sim không có trên SDK (`in_call_audio_mode`, `new_device`).

Họ bất thường g3 không cộng vào mức. Mức vẫn dùng bốn họ quan sát được: cuộc gọi, cách nhập, người nhận, huy động.

### Đối chiếu với 27 cột của booster v2.0

| cột v2.0 | profile v2.1 | ghi chú |
|---|---|---|
| amount, amount_to_balance, new_payee, hour, new_payees_24h, has_tx_signals | basic trở đi | giữ |
| a_w | basic trở đi | quên, đã đưa lại. Công thức sổ 24 giờ vẫn trộn SIMO như v2.0 |
| add_method | basic trở đi | quên, đã đưa lại |
| seconds_since_login | basic trở đi | quên, đã đưa lại |
| payee_name_matches_holder, merchant_verified, has_payee_signals | basic trở đi | tra cứu tên và danh sách tổ chức, không phải PAD |
| pasted, has_device_signals | standard trở đi | quên, đã đưa lại. `pasted` là cờ `paste_account`, không trùng `acct_input` |
| simo_listed | advanced trở đi | giữ |
| on_call, recent_call_minutes_ago, counterparty_is_new, counterparty_number_age_days, counterparty_report_count, has_telco_signals | chỉ telco+PAD | demo không có |
| accessibility_new, remote_access_app, screen_sharing, overlay | chỉ telco+PAD | PAD |
| in_call_audio_mode, new_device | chỉ telco+PAD | demo không có. SDK gửi `in_call`, không gửi audio mode hay tuổi thiết bị |

Cột mới, không có trong booster v2.0: ba đếm sản phẩm 7 ngày, `limit_raise_1d`, `phone_change_7d`, năm cột SDK, bốn cột T4c và bốn cờ thiếu.

## Bảng

Mẫu số tiền là 12.539.478.000 VND. Recall scenario là tỷ lệ phiên lừa có mức banner, Medium hoặc High.

| profile | tiền tránh được | % thiệt hại | recall@1% | MH hợp lệ | Medium vay | cúp máy | τ_hi |
|---|---:|---:|---:|---:|---:|---:|---:|
| basic | 3.279.809.280 [2.331.598.934–4.501.487.192] | 26,16% | 50,00% | 0,43% | 60,52% | 0,27% | 0,3333 |
| standard | 2.336.924.400 [1.518.210.240–3.374.875.679] | 18,64% | 30,10% | 0,18% | 6,23% | 0,27% | 1,0000 |
| advanced | 3.309.467.140 [2.343.082.982–4.501.274.485] | 26,39% | 53,81% | 0,42% | 2,60% | 0,27% | 0,2362 |
| advanced+telco+PAD | 3.026.031.040 [2.115.261.846–4.152.980.763] | 24,13% | 57,74% | 0,42% | 0,52% | 0,27% | 0,1226 |

τ_hi = 1 của standard nghĩa là không nâng thêm: khối xác suất kế tiếp sẽ đẩy Medium+High hợp lệ quá 0,45%.

Tiền tránh được theo mức, VND. Banner không tính.

| profile | medium | high |
|---|---:|---:|
| basic | 793.775.280 | 2.486.034.000 |
| standard | 282.498.900 | 2.054.425.500 |
| advanced | 811.217.640 | 2.498.249.500 |
| advanced+telco+PAD | 761.162.940 | 2.264.868.100 |

Tiền tránh được theo scenario, triệu VND:

| scenario | basic | standard | advanced | telco+PAD |
|---|---:|---:|---:|---:|
| police | 1.744,5 | 1.349,5 | 1.727,1 | 1.645,6 |
| tax_authority | 286,8 | 191,4 | 282,5 | 268,4 |
| bank_staff | 287,8 | 197,2 | 306,7 | 296,5 |
| relative | 78,6 | 62,7 | 81,1 | 83,0 |
| deposit | 13,3 | 4,6 | 7,8 | 7,7 |
| investment | 391,8 | 90,4 | 428,3 | 355,6 |
| romance | 14,1 | 4,8 | 13,4 | 10,0 |
| own_name_safe_account | 462,9 | 436,3 | 462,5 | 359,2 |

Recall theo scenario, test:

| scenario | basic | standard | advanced | telco+PAD |
|---|---:|---:|---:|---:|
| police | 97,1% | 73,7% | 96,4% | 83,2% |
| tax_authority | 94,6% | 55,4% | 87,5% | 85,7% |
| bank_staff | 94,7% | 61,8% | 94,7% | 81,6% |
| relative | 90,2% | 74,5% | 90,2% | 90,2% |
| deposit | 81,8% | 36,4% | 45,5% | 43,6% |
| investment | 91,4% | 74,5% | 73,0% | 59,3% |
| romance | 95,2% | 61,9% | 85,7% | 66,7% |
| own_name_safe_account | 100,0% | 63,6% | 95,5% | 81,8% |

Hiệu số profile trái trừ profile phải. CI 95% paired bootstrap, cùng mẫu, 200 lần, seed 42.

| cặp | tiền (VND) | recall@1% |
|---|---:|---:|
| basic − standard | 942.884.880 [659.033.348–1.391.647.356] | +19,90 điểm [16,27–23,89] |
| basic − advanced | −29.657.860 [−134.864.939–71.043.282] | −3,81 điểm [−7,52–0,00] |
| basic − telco+PAD | 253.778.240 [57.604.532–473.007.231] | −7,74 điểm [−11,77–−3,54] |
| standard − advanced | −972.542.740 [−1.396.837.791–−666.909.928] | −23,71 điểm [−27,65–−19,20] |
| standard − telco+PAD | −689.106.640 [−1.144.122.414–−387.845.234] | −27,64 điểm [−31,68–−23,56] |
| advanced − telco+PAD | 283.436.100 [105.470.284–509.091.211] | −3,93 điểm [−9,37–0,85] |

`tests/test_model_v21.py` nạp ba artifact đã đóng gói, chấm lại test và khớp các số này, sai số 0.

## Giả định

Trong `fides-sim/config.yaml`, mục `decision`, ghi **GIẢ ĐỊNH**:

- c_FP doanh nghiệp nhỏ ×3. c_FP(A) = hệ số × (50.000 + 0,002·A). η = 1.
- Medium giữ tiền 30 phút. High giữ 24 giờ.
- Cúp máy ở high bắt đầu countdown 10 phút. Cuộc gọi mới tính lại countdown. Proceed sau countdown thì nhả. Cancel bất cứ lúc nào. `guardian_confirm` nhả sớm.
- `short_wait` khi người thân hướng dẫn và khách không có giám hộ: 2 phút.
- `p_stop` vẫn là bảng phản ứng cũ, nguồn GIẢ ĐỊNH. Banner không tính vào tiền tránh được.

## Giới hạn

deposit, romance và own_name trên test vẫn ít phiên. Khoảng tin cậy của tiền rộng hơn 1 tỷ VND. Standard có recall@1% 30,1% và τ_hi = 1. Bản telco+PAD không được đưa vào demo. `a_w` trên basic vẫn dùng công thức v2.0, nhánh SIMO nằm trong cùng một số.

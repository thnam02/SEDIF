# FIDES model v2.1

Seed 42. Cùng split: train trước ngày 50, nhãn là báo cáo đã tới hạn; test từ 2026-08-30, nhãn `is_fraud`. Hiệu chỉnh isotonic out-of-fold theo vụ, 5 fold, rồi dịch odds. Ngưỡng τ(A) = c_FP(A) / (c_FP(A) + η·A), η = 1. c_FP doanh nghiệp nhỏ ×3. τ_hi là mốc thấp nhất còn giữ Medium+High trên phiên hợp lệ ≤ 0,45%. CI 95% bootstrap theo vụ, 200 lần, seed 42.

Ba profile được đóng gói ở `engine/artifacts/model-v2.1/<profile>/`: `model.txt`, `policy.json`, `features.json`, `oof.npy`, `SHA256SUMS`. Bản advanced+telco+PAD chỉ có trong bảng, không có thư mục artifact.

## Cột

**basic, 11 cột.** Sổ: `amount`, `amount_to_balance`, `hour`, `new_payee`, `new_payees_24h`, `has_tx_signals`. Sản phẩm 7 ngày: `product_savings_7d`, `product_loan_7d`, `product_cash_advance_7d`. Thay đổi tài khoản: `limit_raise_1d`, `phone_change_7d`. Không device, không telco, không PAD.

**standard, 16 cột.** Basic cộng SDK: `in_call`, `call_seconds_since_open`, `call_ended_in_session`, `acct_input` (`typed`, `typed_chunked`, `pasted`, `chosen`), `app_switches`.

**advanced, 25 cột.** Standard cộng `payee_account_age_days`, `distinct_senders_7d`, `passthrough_24h`, `device_holder_count`, bốn cờ `*_missing`, và `simo_listed`.

**advanced+telco+PAD, 34 cột.** Advanced cộng telco (`on_call`, `recent_call_minutes_ago`, `counterparty_is_new`, `counterparty_number_age_days`, `counterparty_report_count`, `has_telco_signals`) và PAD (`payee_name_matches_holder`, `merchant_verified`, `has_payee_signals`).

Không đưa vào bốn profile: `a_w` vì công thức cũ trộn SIMO, `seconds_since_login`, `pasted`, `add_method`, và các tín hiệu thiết bị ngoài SDK. Họ bất thường g3 không cộng vào mức. Mức vẫn dùng bốn họ quan sát được: cuộc gọi, cách nhập, người nhận, huy động.

## Bảng

Mẫu số tiền là 12.539.478.000 VND, tổng tiền phiên lừa trên test. Recall là tỷ lệ phiên lừa của scenario có mức banner, Medium hoặc High. Medium vay và cúp máy là trên test.

| profile | tiền tránh được | % thiệt hại | recall@1% | MH hợp lệ | Medium vay | cúp máy | τ_hi |
|---|---:|---:|---:|---:|---:|---:|---:|
| basic | 2.313.603.620 [1.511.924.997–3.326.437.012] | 18,5% [12,1–26,5] | 30,1% | 0,44% | 4,7% | 0,3% | 0,2083 |
| standard | 2.547.352.860 [1.655.449.411–3.576.597.096] | 20,3% [13,2–28,5] | 32,1% | 0,40% | 4,7% | 0,3% | 0,4308 |
| advanced | 2.075.856.660 [1.434.782.720–3.000.176.100] | 16,6% [11,4–23,9] | 40,7% | 0,44% | 1,6% | 0,0% | 0,2823 |
| advanced+telco+PAD | 2.449.947.080 [1.498.859.141–3.575.212.710] | 19,5% [12,0–28,5] | 35,6% | 0,13% | 1,0% | 0,0% | 1,0000 |

τ_hi = 1 của bản lộ trình nghĩa là không nâng thêm: khối xác suất kế tiếp sẽ đẩy Medium+High hợp lệ quá 0,45%.

Recall theo scenario, test:

| scenario | basic | standard | advanced | advanced+telco+PAD |
|---|---:|---:|---:|---:|
| police | 70,8% | 72,3% | 66,4% | 67,9% |
| tax_authority | 75,0% | 60,7% | 57,1% | 62,5% |
| bank_staff | 81,6% | 68,4% | 72,4% | 72,4% |
| relative | 58,8% | 70,6% | 62,7% | 54,9% |
| deposit | 25,5% | 30,9% | 27,3% | 27,3% |
| investment | 55,8% | 64,1% | 65,9% | 36,6% |
| romance | 76,2% | 61,9% | 47,6% | 28,6% |
| own_name_safe_account | 72,7% | 90,9% | 63,6% | 95,5% |

`tests/test_model_v21.py` nạp ba artifact đã đóng gói, chấm lại test và khớp các số này, sai số 0.

## Giả định

Trong `fides-sim/config.yaml`, mục `decision`, ghi **GIẢ ĐỊNH**:

- c_FP doanh nghiệp nhỏ ×3. c_FP(A) = hệ số × (50.000 + 0,002·A). η = 1.
- Medium giữ tiền 30 phút. High giữ 24 giờ.
- Cúp máy ở high bắt đầu countdown 10 phút. Cuộc gọi mới tính lại countdown. Proceed sau countdown thì nhả. Cancel bất cứ lúc nào. `guardian_confirm` nhả sớm.
- `short_wait` khi người thân hướng dẫn và khách không có giám hộ: 2 phút.
- `p_stop` vẫn là bảng phản ứng cũ, nguồn GIẢ ĐỊNH. Banner không tính vào tiền tránh được.

## Giới hạn

deposit, romance, own_name và tax trên test vẫn ít phiên. Khoảng tin cậy của tiền rộng hơn 1 tỷ VND. Advanced tăng recall@1% lên 40,7% nhưng tiền tránh được thấp hơn standard. Bản telco+PAD không được đưa vào demo.

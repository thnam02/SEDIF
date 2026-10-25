# FIDES model v2.1

Seed của split là 42. Train trước ngày 50. Test từ 2026-08-30, nhãn `is_fraud`. Artifact demo ở `engine/artifacts/model-v2.1/advanced_ctx_payee/`.

Profile demo là **advanced_ctx_payee**. LightGBM dùng cột ngân hàng và T4c, 28 cột, không có năm cột SDK (`in_call`, `call_seconds_since_open`, `call_ended_in_session`, `acct_input`, `app_switches`). SDK chỉ vào luật họ call và entry. Họ payee chỉ bật khi người nhận mới với người gửi và tài khoản nhận không bình thường theo T4c. Bình thường = `payee_account_age_days` ≥ 365, `distinct_senders_7d` ≤ p90 hợp lệ train (6), `passthrough_24h` ≤ p90 hợp lệ train (0,951). Thiếu một trong ba thì họ vẫn bật.

Chọn trong tám biến thể: tiền ≥ 99% bản tốt nhất, rồi Medium người nhà + vay bạn thấp nhất, rồi recall@1%, rồi sd tiền. Bản tốt nhất về tiền là advanced, 3.265.784.072 VND. `basic_payee` và `standard_payee` rơi khỏi cửa vì investment ≥ Medium tụt còn khoảng 24%. Artifact là seed 45, seed gần trung bình của `advanced_ctx_payee`, không phải seed recall cao nhất.

`tests/test_model_v21.py` chấm lại artifact và khớp bảng đóng gói, sai số 0.

## Năm seed, mean ± sd

Tiền kèm phần trăm so với trần của chính biến thể đó. Trần là tiền nếu mọi phiên lừa được đưa lên ít nhất Medium.

| biến thể | tiền | % trần | recall@1% | MH | Medium nhà | Medium vay | cúp máy | ≥Med police | investment | own_name | relative |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| basic | 3.263.432.264 ± 1.645.943 | 92,0% | 42,21% ± 1,05 | 0,12% | 12,88% ± 0,24 | 22,34% | 0,27% | 87,6% | 57,2% ± 1,5 | 95,5% | 92,2% ± 2,0 |
| basic_payee | 2.999.080.988 ± 1.702.260 | 84,9% | 42,21% ± 1,05 | 0,10% | 3,64% ± 0,31 | 22,08% | 0,00% | 84,7% | 24,3% ± 0,2 | 90,9% | 72,5% ± 1,4 |
| standard | 3.265.783.760 ± 0 | 92,1% | 47,08% ± 0,80 | 0,21% ± 0,02 | 55,60% ± 8,39 | 22,34% | 0,27% | 87,6% | 59,3% | 95,5% | 94,1% |
| standard_payee | 2.948.254.532 ± 105.468.126 | 83,5% | 47,08% ± 0,80 | 0,13% ± 0,01 | 15,11% ± 3,79 | 22,08% | 0,00% | 82,8% ± 2,5 | 26,4% ± 6,7 | 88,2% ± 4,1 | 64,7% ± 9,1 |
| advanced | 3.265.784.072 ± 376 | 92,1% | 70,29% ± 0,97 | 0,22% ± 0,07 | 49,08% ± 19,11 | 21,82% ± 0,61 | 0,27% | 88,0% ± 0,4 | 59,3% | 95,5% | 94,1% |
| advanced_payee | 3.249.964.184 ± 1.144.304 | 92,0% | 70,29% ± 0,97 | 0,14% ± 0,02 | 18,70% ± 3,05 | 21,56% ± 0,61 | 0,00% | 88,0% ± 0,4 | 57,4% ± 1,4 | 95,5% | 94,1% |
| advanced_ctx | 3.265.783.904 ± 322 | 92,1% | 61,18% ± 3,09 | 0,16% ± 0,05 | 17,17% ± 5,55 | 22,23% ± 0,14 | 0,27% | 87,7% ± 0,3 | 59,3% | 95,5% | 94,1% |
| advanced_ctx_payee | 3.250.113.680 ± 275.743 | 92,0% | 61,18% ± 3,09 | 0,13% ± 0,01 | 13,10% ± 0,62 | 21,97% ± 0,14 | 0,00% | 87,7% ± 0,3 | 57,6% ± 0,7 | 95,5% | 94,1% |

## Artifact seed 45

Tiêu chí chọn τ_hi: ngưỡng lớn nhất còn giữ tiền ≥ 99,5% mức tối đa dưới trần Medium+High hợp lệ 0,45%, và ≥Medium của từng scenario giảm không quá 1 điểm so với mức tối đa đó. Mức tối đa là 3.251.223.920 VND.

τ_hi đóng gói là **0,8125**. Bậc kế tiếp của hiệu chỉnh isotonic là 0,8182. Bậc đó vẫn giữ 99,66% tiền tối đa (3.240.287.120 VND) nhưng ≥Medium của investment giảm 7,07 điểm (58,08% → 51,01%) và deposit giảm 7,28 điểm (52,73% → 45,45%). Vì vậy giữ 0,8125.

Tiền 3.250.266.440 VND, 92,0% trần 3.531.704.180. Recall@1% 60,81%. MH hợp lệ 0,12%. Medium người nhà 12,50%. Medium vay bạn 22,08%. Cúp máy người nhà 0%. ≥ Medium: police 87,6%, investment 58,1%, own_name 95,5%, relative 94,1%. 101 vòng. Ngưỡng ghi trong `policy.json` và `policy.yaml`.

## Giới hạn

Hiệu chỉnh isotonic tạo bậc điểm. Kết quả nhạy với τ_hi quanh 0,81–0,82: một bậc làm investment và deposit mất khoảng 7 điểm ≥Medium.

Vay bạn Medium ~22%. 86/385 phiên vay bạn có đúng hai họ, toàn bộ là entry+payee (50) hoặc call+payee (36). 81 phiên còn Medium vì thiếu passthrough, 4 vì tuổi dưới 365 và thiếu passthrough, 1 bị tắt vì đủ bình thường. Luật ≥2 họ đặt các phiên còn lại ở Medium trước khi τ_hi chạy.

Bản sửa “ngân hàng khác chỉ cần tuổi và senders” không đạt cửa. Năm seed: tiền 2.930.056.516 ± 6.990.602 (90,8% trần, 89,7% bản tốt nhất), recall@1% 61,18% ± 3,09, Medium vay bạn 6,03% ± 1,64. ≥Medium so với advanced_ctx_payee: police +0,0 điểm, investment −10,1 điểm, own_name +0,0 điểm, relative −1,2 điểm. Giữ artifact seed 45.

# FIDES model v2.0

Tag git: `model-v2.0` (commit `383c7ac`). Hợp đồng phục vụ: API v2.1. Seed 42. Cùng tập test: phiên có `ts` từ ngày 60 (2026-08-30) trở đi. Nhãn test là `is_fraud`. CI 95% bootstrap theo vụ, 200 lần, seed 42.

Bản được đóng gói: **không p95, có c_FP doanh nghiệp nhỏ ×3, không T4c** (27 cột). τ_hi của bản này là 0,3148. g3 không vào LightGBM; g3 chỉ là họ thứ 5 khi điểm OOF của phiên hợp lệ train đạt p99,5.

## Bảng ablation

Mỗi bản quét lại τ_hi sao cho Medium+High trên mọi phiên hợp lệ không quá 0,45%. Tiền tránh được chỉ tính trên test, với hành động mạnh hơn banner, nhân `p_stop` trong mô hình phản ứng.

| p95 | fp×3 | T4c | cột | tiền tránh được (VND) | % thiệt hại | recall@1% | DN prec | DN rec | Medium vay | cúp máy |
|---|---|---|---:|---|---|---:|---:|---:|---:|---:|
| không | không | không | 27 | 3.138.124.500 [2.223.145.476–4.369.173.014] | 25,0% [20,2–30,5] | 56,6% | 13,3% | 82,5% | 44,2% | 0,3% |
| không | không | có | 35 | 3.292.853.900 [2.346.248.112–4.450.609.443] | 26,3% [21,7–31,3] | 64,1% | 10,9% | 73,8% | 3,9% | 0,3% |
| không | có | không | 27 | 3.187.995.900 [2.277.231.708–4.431.169.468] | 25,4% [20,8–30,8] | 56,6% | 18,2% | 74,8% | 50,6% | 0,3% |
| không | có | có | 35 | 3.290.818.380 [2.346.180.962–4.449.439.730] | 26,2% [21,7–31,3] | 64,1% | 20,3% | 65,0% | 3,9% | 0,3% |
| có | không | không | 28 | 2.649.528.000 [1.728.277.844–3.739.534.908] | 21,1% [16,0–26,5] | 39,2% | 16,2% | 49,5% | 6,8% | 0,0% |
| có | không | có | 36 | 3.077.304.360 [2.155.048.337–4.245.300.394] | 24,5% [19,8–29,8] | 56,1% | 18,4% | 65,0% | 6,2% | 0,0% |
| có | có | không | 28 | 2.648.835.480 [1.728.277.844–3.738.842.388] | 21,1% [16,0–26,5] | 39,2% | 19,6% | 45,6% | 6,8% | 0,0% |
| có | có | có | 36 | 3.076.387.920 [2.155.025.426–4.244.383.954] | 24,5% [19,8–29,7] | 56,1% | 27,9% | 61,2% | 5,7% | 0,0% |

Bản T4c, không p95, không ×3 có điểm tiền tránh được cao nhất (3,29 tỷ). Khoảng tin cậy của nó chồng với các bản 27 cột, nên chọn bản ít cột hơn. Trong hai bản 27 cột, bản có ×3 tránh được nhiều tiền hơn.

Thiệt hại test của mẫu số là tổng số tiền các phiên lừa đảo trên test, 12.539.478.000 VND. recall@1% là recall không trọng số của điểm thô LightGBM. Precision và recall doanh nghiệp nhỏ tính trên mọi phiên đã chấm (train out-of-fold, đệm, test): can thiệp gồm banner, Medium và High. Medium vay là tỷ lệ Medium trên `legit_friend_loan` của test. Cúp máy là tỷ lệ `hangup_required` trên `legit_guided_by_relative` của test.

## Cách chấm

- LightGBM: 200 cây, learning rate 0,08, 31 lá, `min_child_samples` 40, subsample và column sample 0,8, seed 42. Cột là đặc trưng một ngân hàng, không gồm năm cột dài hạn, không gồm p95, không gồm bốn đặc trưng mạng lưới.
- Xác suất: isotonic trên điểm out-of-fold theo vụ (5 fold, seed 42), học trên `is_fraud`, rồi dịch odds để trung bình khớp tỷ lệ lừa đảo của train.
- Ngưỡng tiền: τ(A) = c_FP(A) / (c_FP(A) + η·A), η = 1, c_FP(A) = hệ số × (50.000 + 0,002·A). Vượt τ mới can thiệp. Hai họ là Medium, từ ba họ là High, ít hơn là banner.
- τ_hi: nếu p hiệu chỉnh ≥ τ_hi thì phiên có 0–1 họ vẫn được tối thiểu Medium. Chọn τ_hi thấp nhất còn giữ Medium+High trên phiên hợp lệ ≤ 0,45%.
- Họ thứ 5: điểm g3 (trung bình Isolation Forest và CDF của CUSUM 30 ngày), forest chỉ học phiên hợp lệ và chấm train theo fold không chứa phiên đó. Ngưỡng là p99,5 của phiên hợp lệ train.
- Cao tuổi có giám hộ: Medium thành `guardian_confirm`. High là `hangup_required`. Banner là `g1_banner`. Không can thiệp là `allow`.

## Giả định

Các mục sau được ghi **GIẢ ĐỊNH** trong `fides-sim/config.yaml`. Không ước lượng từ dữ liệu thật.

- c_FP của doanh nghiệp nhỏ nhân 3, vì giả định một lần báo động sai làm gián đoạn dòng tiền kinh doanh.
- `p_stop`, `p_abandon`, `p_complaint` lấy từ `response_model`. Banner không được tính vào tiền tránh được. Tiền tránh được là kỳ vọng `số tiền × p_stop`, không phải số tiền đã chặn trong một lần mô phỏng phản ứng.
- Segment đưa vào bảng phản ứng chỉ có `thường` và `cao tuổi`. Doanh nghiệp nhỏ dùng hàng `thường` khi tra `p_stop`.
- η = 1: cả khoản tiền đang xét đều là thiệt hại nếu vụ là lừa đảo và khách không dừng.

## Giới hạn

- **deposit** có 55 phiên trên test, **romance** có 21. Hai ô này quá nhỏ để kết luận tỷ lệ High hoặc Medium. Trên chính sách một ngân hàng trước khi nhân c_FP, High của cả hai loại là 0% và khoảng tin cậy rất rộng. `own_name_safe_account` (22), `relative` (51) và `tax_authority` (56) cũng mỏng.
- Khoảng tin cậy của tiền tránh được rộng khoảng ±1 tỷ VND. Các bản 27 cột và 35 cột không tách khỏi nhau. Chọn 27 cột vì ít cột hơn khi các khoảng chồng nhau, không phải vì bản đó thắng rõ về tiền.
- Cột p95 90 ngày làm recall@1% tụt từ 56,6% xuống 39,2% và làm tiền tránh được giảm. Không đưa vào gói.
- T4c nâng recall@1% lên 64,1% và nâng điểm tiền tránh được, nhưng khoảng tin cậy vẫn chồng bản ít cột. Không đưa vào gói.
- Medium trên `legit_friend_loan` của bản đã chốt là 50,6%. Đó là mẫu khó: nhiều phiên hợp lệ vẫn bị yêu cầu dừng để xác minh.
- Cúp máy người nhà hướng dẫn là 0,3% trên 368 phiên test. Một vài phiên vẫn có thể bị `hangup_required`.
- Nhãn train là báo cáo đã tới hạn, không phải mọi vụ lừa đảo. Hiệu chỉnh dùng `is_fraud` của các phiên train để đưa xác suất về tỷ lệ thật, nhưng mô hình vẫn học trên nhãn trễ.
- Dữ liệu là sổ mô phỏng một ngân hàng. Gói này không phải mô hình đã kiểm chứng trên giao dịch thật.

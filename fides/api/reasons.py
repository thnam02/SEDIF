"""Ba câu tiếng Việt từ đóng góp SHAP lớn nhất."""

PHRASE = {
    "amount": "Số tiền giao dịch",
    "amount_to_balance": "Tỷ lệ số tiền trên số dư",
    "a_w": "Số tiền gửi người nhận mới hoặc tài khoản trong danh sách SIMO",
    "new_payee": "Người nhận mới",
    "pasted": "Dán số tài khoản",
    "add_method": "Cách nhập người nhận",
    "hour": "Giờ trong ngày",
    "seconds_since_login": "Thời gian từ lúc đăng nhập",
    "new_payees_24h": "Số người nhận mới trong 24 giờ",
    "has_tx_signals": "Đủ dữ liệu giao dịch",
    "payee_name_matches_holder": "Tên người nhận trùng chủ tài khoản",
    "merchant_verified": "Điểm thu đã xác minh",
    "simo_listed": "Tài khoản nhận nằm trong danh sách SIMO",
    "has_payee_signals": "Đủ dữ liệu người nhận",
    "in_call_audio_mode": "Máy đang trong cuộc gọi",
    "accessibility_new": "Quyền trợ năng vừa được bật",
    "remote_access_app": "Có ứng dụng điều khiển từ xa",
    "screen_sharing": "Đang chia sẻ màn hình",
    "overlay": "Có lớp phủ trên màn hình",
    "new_device": "Thiết bị mới",
    "has_device_signals": "Đủ tín hiệu thiết bị",
    "on_call": "Nhà mạng thấy đang nghe máy",
    "recent_call_minutes_ago": "Cuộc gọi gần đây",
    "counterparty_is_new": "Số điện thoại đầu dây là số mới",
    "counterparty_number_age_days": "Tuổi của số điện thoại đầu dây",
    "counterparty_report_count": "Số lần số điện thoại bị tố cáo",
    "has_telco_signals": "Đủ nhật ký nhà mạng",
}


def reasons_from_contrib(columns: list[str], contrib) -> list[str]:
    order = sorted(range(len(columns)), key=lambda index: abs(float(contrib[index])), reverse=True)
    lines = []
    for index in order:
        if len(lines) == 3:
            break
        weight = float(contrib[index])
        if weight == 0.0:
            continue
        name = columns[index]
        phrase = PHRASE.get(name, name)
        direction = "kéo điểm lừa đảo lên" if weight > 0 else "kéo điểm lừa đảo xuống"
        lines.append(f"{phrase} {direction}.")
    return lines

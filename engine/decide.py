"""Luật mức của hợp đồng /decide và /respond. Không đọc sổ, không đọc truth."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
LEVELS = ["none", "info", "banner", "medium", "high"]
HOLD_MEDIUM = timedelta(minutes=30)
HOLD_HIGH = timedelta(hours=24)
COUNTDOWN = timedelta(minutes=10)
SENDERS_P90 = 6.0
PASSTHROUGH_P90 = 0.9510390587806167
SENDERS_P99 = 10.0
PASSTHROUGH_P99 = 1.0
TAU_HI = 0.8125

REASONS = (
    ("call", "in_call", "Cuộc gọi đang mở trong phiên này."),
    ("call_ended", "call_ended", "Cuộc gọi vừa kết thúc trong phiên này."),
    ("entry", "acct_pasted", "Số tài khoản được dán vào ô chuyển tiền."),
    ("payee", "new_payee", "Chưa từng chuyển tới người nhận này."),
    ("funding", "mobilization", "Có giao dịch rút sổ, giải ngân hoặc ứng tiền mặt."),
    (
        "simo",
        "simo_listed",
        "Tài khoản nhận đã bị báo cáo có dấu hiệu liên quan đến lừa đảo. Cơ quan công an và ngân hàng không bao giờ yêu cầu chuyển tiền vào tài khoản 'tạm giữ' hay 'xác minh'. Nếu ai đó đang hướng dẫn bạn chuyển khoản, hãy dừng lại.",
    ),
    (
        "simo_info",
        "simo_info",
        "Bạn đã chuyển cho người nhận này từ trước khi tài khoản được đưa vào danh sách cần chú ý.",
    ),
)

BASIC_MISSING = (
    "payee_account_age_days",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
    "in_call_audio_mode",
    "accessibility_new",
    "remote_access_app",
    "screen_sharing",
    "overlay",
    "new_device",
    "has_device_signals",
    "on_call",
    "recent_call_minutes_ago",
    "counterparty_is_new",
    "counterparty_number_age_days",
    "counterparty_report_count",
    "has_telco_signals",
)


def parse_ts(value: str) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ICT)
    return stamp.astimezone(ICT)


def to_us(stamp: datetime) -> int:
    return int(stamp.timestamp() * 1_000_000)


def iso(stamp: datetime | None) -> str | None:
    if stamp is None:
        return None
    return stamp.astimezone(ICT).isoformat(timespec="microseconds")


def _num(value) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def cost_tau(amount: float, scale: float) -> float:
    paid = 0.0 if amount != amount or amount < 0 else float(amount)
    false_positive = (50_000.0 + 0.002 * paid) * scale
    return false_positive / (false_positive + paid)


def gated_families(row: dict) -> dict[str, int]:
    """Họ payee tắt khi người nhận mới nhưng T4c strict coi là bình thường."""
    call = int(row.get("call_family") or 0)
    entry = int(row.get("entry_family") or 0)
    funding = int(row.get("funding_family") or 0)
    payee = int(row.get("payee_family") or 0)
    age = _num(row.get("payee_account_age_days"))
    senders = _num(row.get("distinct_senders_7d"))
    passed = _num(row.get("passthrough_24h"))
    normal = age == age and age >= 365.0 and senders == senders and senders <= SENDERS_P90 and passed == passed and passed <= PASSTHROUGH_P90
    if payee and normal:
        payee = 0
    return {"call": call, "entry": entry, "payee": payee, "mobilization": funding}


def eligible(row: dict, families: dict[str, int]) -> bool:
    if families["call"] or families["entry"] or families["mobilization"]:
        return True
    senders = _num(row.get("distinct_senders_7d"))
    passed = _num(row.get("passthrough_24h"))
    senders_hit = senders == senders and senders > SENDERS_P99
    passed_hit = passed == passed and passed > PASSTHROUGH_P99
    return bool(senders_hit or passed_hit)


def simo_repeat(row: dict, families: dict[str, int]) -> bool:
    """≥3 lần trước khi liệt kê, lần đầu cách lúc liệt kê ≥60 ngày, không họ thao túng."""
    if int(row.get("simo_listed") or 0) != 1 or sum(families.values()) > 0:
        return False
    if int(row.get("payee_transfers_before_listing") or 0) < 3:
        return False
    return float(row.get("payee_first_days_before_listing") or 0) >= 60


def assign_level(probability: float, amount: float, families: dict[str, int], segment: str, row: dict) -> str:
    scale = 3.0 if segment == "doanh nghiệp nhỏ" else 1.0
    count = sum(families.values())
    fired = probability >= cost_tau(amount, scale)
    if not fired:
        level = "none"
    elif count >= 3:
        level = "high"
    elif count == 2:
        level = "medium"
    else:
        level = "banner"
    if probability >= TAU_HI and eligible(row, families) and count <= 1 and level in {"none", "banner"}:
        level = "medium"
    if int(row.get("simo_listed") or 0) == 1 and count == 0:
        if simo_repeat(row, families):
            return "info"
        if level == "none":
            return "banner"
    return level


def reasons_for(row: dict, families: dict[str, int]) -> list[dict[str, str]]:
    picked = []
    if families["call"]:
        if int(row.get("in_call") or 0) == 1:
            picked.append({"code": "in_call", "text": REASONS[0][2]})
        else:
            picked.append({"code": "call_ended", "text": REASONS[1][2]})
    if families["entry"]:
        picked.append({"code": "acct_pasted", "text": REASONS[2][2]})
    if families["payee"]:
        picked.append({"code": "new_payee", "text": REASONS[3][2]})
    if families["mobilization"]:
        picked.append({"code": "mobilization", "text": REASONS[4][2]})
    if simo_repeat(row, families):
        picked.append({"code": "simo_info", "text": REASONS[6][2]})
    elif int(row.get("simo_listed") or 0) == 1:
        simo = {"code": "simo_listed", "text": REASONS[5][2]}
        if len(picked) >= 3:
            picked = picked[:2]
        picked.append(simo)
    return picked[:3]


def missing_signals(row: dict, columns: list[str]) -> list[str]:
    return [name for name in columns if row.get(name) is None and not name.endswith("_missing")]


def profile_missing(profile: str) -> list[str]:
    if profile == "basic":
        return list(BASIC_MISSING)
    return []


def hold_for(level: str, decided: datetime) -> tuple[str, datetime | None]:
    if level == "medium":
        return "held", decided + HOLD_MEDIUM
    if level == "high":
        return "held", decided + HOLD_HIGH
    return "released", None


def apply_contract(state: dict, body: dict, now: datetime) -> dict:
    """Không hạ level. authority và bank lên high. relative chỉ đặt gate."""
    level = state["level"]
    who = body.get("who_guides")
    if who in {"authority", "bank"}:
        level = "high"
    if LEVELS.index(level) < LEVELS.index(state["level"]):
        level = state["level"]
    funds, hold = hold_for(level, state["decided_at"])
    if level in {"medium", "high"}:
        funds = "held" if state["funds_status"] != "cancelled" else "cancelled"
    elif state["funds_status"] == "cancelled":
        funds = "cancelled"
    gate = "none"
    if who == "relative":
        gate = "guardian_confirm_requested" if state["has_guardian"] else "short_wait"
    countdown = state["countdown_until"]
    if body.get("hung_up") and level == "high":
        countdown = now + COUNTDOWN
        if funds != "cancelled":
            funds = "held"
    if body.get("guardian_confirm"):
        if funds != "cancelled":
            funds = "released"
    if body.get("proceed") and countdown is not None and now >= countdown and funds != "cancelled":
        funds = "released"
    if body.get("cancel"):
        funds = "cancelled"
    return {
        "level": level,
        "funds_status": funds,
        "gate": gate,
        "hold_until": hold if level in {"medium", "high"} else None,
        "countdown_until": countdown,
    }

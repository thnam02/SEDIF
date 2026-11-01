"""Chính sách /decide v2.4. Đọc bank_policy.yaml. Không đổi model, không đọc truth."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from engine.decide import REASONS

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
log = logging.getLogger("engine.policy")
_LOCK = threading.Lock()
_SAMPLES: list[float] = []
_POLICY: dict | None = None

STRING_LEVEL = {0: "none", 1: "banner", 2: "medium", 3: "high"}
INTERVENTION = {0: "none", 1: "banner", 2: "three_button", 3: "security_workflow"}
BASELINE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
SIMO_TEXT = REASONS[5][2]
SIMO_INFO_TEXT = REASONS[6][2]
CALL_NEWPAYEE_TEXT = "Bạn đang trong cuộc gọi và chuyển tiền cho người nhận mới"
VERIFIED_SOURCES = {
    "recipient_bank_confirmed_mule": {"core_banking"},
    "recipient_le_confirmed_case": {"core_banking"},
}
SIGNAL_ORDER = (
    "has_call",
    "entry_paste_or_qr",
    "beneficiary_is_new",
    "recent_funds_event",
    "purpose_high_risk",
    "recipient_unusual",
    "recipient_simo_flag",
    "model_p_high",
    "model_p_cost",
    "behaviour_rare",
    "screen_capture_active",
    "device_capability",
    "verified_compromise",
    "recipient_bank_confirmed_mule",
    "recipient_le_confirmed_case",
)


def load_policy() -> dict:
    global _POLICY
    if _POLICY is None:
        raw = yaml.safe_load(Path(__file__).with_name("bank_policy.yaml").read_text(encoding="utf-8"))
        _POLICY = raw
    return _POLICY


def threshold(name: str) -> float:
    item = load_policy()["thresholds"][name]
    return item["value"]


def _note_latency(ms: float) -> None:
    with _LOCK:
        _SAMPLES.append(ms)
        if len(_SAMPLES) > 512:
            del _SAMPLES[: len(_SAMPLES) - 512]
        ordered = sorted(_SAMPLES)
        count = len(ordered)

        def pick(pct: float) -> float:
            index = min(count - 1, max(0, int(round(pct * (count - 1)))))
            return ordered[index]

        log.info(
            "policy scoring p50=%.3fms p95=%.3fms p99=%.3fms n=%d",
            pick(0.50),
            pick(0.95),
            pick(0.99),
            count,
        )


@dataclass
class Signal:
    signal_id: str
    status: str
    tier: str
    confidence: float
    source: str
    observed_at: str
    freshness_s: int

    def as_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "status": self.status,
            "tier": self.tier,
            "confidence": self.confidence,
            "source": self.source,
            "observed_at": self.observed_at,
            "freshness_s": self.freshness_s,
        }


@dataclass
class Facts:
    sender: str
    payee: str
    amount: int
    ts: datetime
    call_state: str | None = None
    task_type: str | None = None
    transfer_purpose: str | None = None
    device_state: str | None = None
    funds_events: list[dict] | None = None
    new_payee: bool | None = None
    payee_age_days: float | None = None
    distinct_senders_7d: float | None = None
    passthrough_24h: float | None = None
    merchant_verified: bool | None = None
    simo_listed: bool | None = None
    prior_payments: int | None = None
    first_days_before_listing: float | None = None
    p_fraud: float | None = None
    segment: str = "thường"
    behaviour_p_rarity: float | None = None
    same_task_sessions: int | None = None
    limit_per_txn: int | None = None
    limit_per_day: int | None = None
    spent_today: int | None = None
    verified: list[dict] = field(default_factory=list)
    user_level_floor: int = 0


@dataclass
class Outcome:
    level: int
    rules_fired: list[str]
    signals: list[Signal]
    reasons_vi: list[str]
    reason_codes: list[str]
    baseline_quality: str
    episode_id: str
    intervention: str
    extra_auth: bool
    split_suspected: bool
    cumulative_amount: int
    funds_status: str
    hold_until: datetime | None
    score_ms: float
    behaviour_score: float | None = None
    behaviour_p_rarity: float | None = None

    @property
    def evidence_card(self) -> dict:
        return {
            "signals": [item.as_dict() for item in self.signals],
            "reasons_vi": list(self.reasons_vi),
            "baseline_quality": self.baseline_quality,
            "episode_id": self.episode_id,
            "behaviour_score": self.behaviour_score,
            "p_rarity": self.behaviour_p_rarity,
        }

    def reason_rows(self) -> list[dict[str, str]]:
        return [{"code": code, "text": text} for code, text in zip(self.reason_codes, self.reasons_vi, strict=True)]


# Tên trên hợp đồng. Luật vẫn đọc mã trong bank_policy.yaml.
_CONTRACT_SIGNAL = {
    "has_call": "call_active",
    "recent_funds_event": "funds_mobilised",
    "beneficiary_is_new": "new_payee",
}


def contract_code(signal_id: str, call_state: str | None = None) -> str:
    """Mã lý do ổn định, cùng tên với signals_present."""
    if signal_id == "has_call" and call_state == "ended_in_session":
        return "call_ended"
    return _CONTRACT_SIGNAL.get(signal_id, signal_id)


def signals_present(outcome: Outcome, call_state: str | None = None) -> list[str]:
    """Tín hiệu chính sách đang present. Gồm cả trường trên request và dòng trong sổ đặc trưng."""
    names: list[str] = []
    for signal in outcome.signals:
        if signal.status != "present":
            continue
        names.append(contract_code(signal.signal_id, call_state))
    return names


class MemoryState:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def load(self, sender: str) -> dict:
        raw = self.rows.get(sender)
        return json.loads(json.dumps(raw)) if raw else _blank()

    def save(self, sender: str, doc: dict) -> None:
        self.rows[sender] = json.loads(json.dumps(doc))


class RedisState:
    """Kho episode trên Redis db đang mở (db 15). Không flush."""

    def __init__(self, client) -> None:
        self.client = client

    def load(self, sender: str) -> dict:
        raw = self.client.get(f"pol:v24:{sender}")
        if not raw:
            return _blank()
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return _blank()
        blank = _blank()
        blank.update(loaded)
        return blank

    def save(self, sender: str, doc: dict) -> None:
        self.client.set(f"pol:v24:{sender}", json.dumps(doc, ensure_ascii=False))


def _blank() -> dict:
    return {
        "episode_id": "",
        "floor": 0,
        "opened_at": "",
        "expires_at": "",
        "payee": "",
        "funds": [],
        "tallies": [],
        "customer_floor": 0,
        "customer_until": "",
    }


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ICT)
    return value.astimezone(ICT).isoformat()


def _parse(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ICT)
    return stamp.astimezone(ICT)


def _when(facts: Facts) -> datetime:
    stamp = facts.ts
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ICT)
    return stamp.astimezone(ICT)


def _signal(signal_id: str, status: str, tier: str, source: str, observed: datetime, now: datetime) -> Signal:
    freshness = max(0, int((now - observed).total_seconds()))
    confidence = 1.0 if status == "present" else 0.0
    return Signal(signal_id, status, tier, confidence, source, _stamp(observed), freshness)


def _tri(present: bool | None) -> str:
    if present is None:
        return "unknown"
    return "present" if present else "absent"


def _baseline(sessions: int | None) -> str:
    if sessions is None:
        return "none"
    block = load_policy().get("behavioural_baseline") or {}
    high = int(block.get("high_sessions", threshold("behaviour_baseline_high_sessions")))
    medium = int(block.get("medium_sessions", threshold("behaviour_baseline_medium_sessions")))
    count = int(sessions)
    if count >= high:
        return "high"
    if count >= medium:
        return "medium"
    if count > 0:
        return "low"
    return "none"


def _fresh_funds(facts: Facts, now: datetime) -> list[dict]:
    if facts.funds_events is None:
        return []
    window = timedelta(hours=float(threshold("funds_fresh_hours")))
    names = load_policy()["funds_types"]
    kept = []
    for event in facts.funds_events:
        kind = str(event.get("type") or "")
        if kind not in names:
            continue
        if event.get("channel") not in {"app", "branch"}:
            continue
        raw = event.get("at")
        if not raw:
            continue
        at = _parse(str(raw))
        if now - window <= at <= now:
            kept.append({"type": kind, "at": _stamp(at), "channel": event["channel"]})
    return kept


def _funds_keys(events: list[dict]) -> list[str]:
    return [f"{item['type']}|{item['at']}" for item in events]


def build_signals(facts: Facts, now: datetime) -> tuple[dict[str, Signal], dict[str, str], list[dict]]:
    observed = now
    call = facts.call_state
    if call in {"active", "ended_in_session"}:
        has_call = "present"
    elif call == "none":
        has_call = "absent"
    else:
        has_call = "unknown"
    task = facts.task_type
    if task in {"new_pasted", "qr"}:
        entry = "present"
    elif task in {"saved_payee", "new_typed"}:
        entry = "absent"
    else:
        entry = "unknown"
    purpose = facts.transfer_purpose
    if purpose in {"authority_request", "fee_to_receive"}:
        purpose_status = "present"
    elif purpose in {"investment", "refund_request", "purchase", "family_friends", "debt_salary", "other"}:
        purpose_status = "absent"
    else:
        purpose_status = "unknown"
    funds = _fresh_funds(facts, now)
    if facts.funds_events is None:
        funds_status = "unknown"
    else:
        funds_status = "present" if funds else "absent"
    unusual = _unusual(facts)
    behaviour = _behaviour(facts)
    capture, capability, device_verified = _device(facts.device_state)
    mule, case = _verified(facts.verified)
    signals = {
        "has_call": _signal("has_call", has_call, "contextual", "sdk", observed, now),
        "entry_paste_or_qr": _signal("entry_paste_or_qr", entry, "contextual", "sdk", observed, now),
        "beneficiary_is_new": _signal("beneficiary_is_new", _tri(facts.new_payee), "contextual", "core_banking", observed, now),
        "recent_funds_event": _signal("recent_funds_event", funds_status, "contextual", "core_banking", _funds_observed(funds, now), now),
        "purpose_high_risk": _signal("purpose_high_risk", purpose_status, "suspicious", "user_answer", observed, now),
        "recipient_unusual": _signal("recipient_unusual", unusual, "suspicious", "core_banking", observed, now),
        "recipient_simo_flag": _signal("recipient_simo_flag", _tri(facts.simo_listed), "suspicious", "simo", observed, now),
        "model_p_high": _signal("model_p_high", _model(facts.p_fraud), "suspicious", "model", observed, now),
        "model_p_cost": _signal("model_p_cost", _model_cost(facts), "suspicious", "model", observed, now),
        "behaviour_rare": _signal("behaviour_rare", behaviour, "suspicious", "sdk", observed, now),
        "screen_capture_active": _signal("screen_capture_active", capture, "suspicious", "device_attestation", observed, now),
        "device_capability": _signal("device_capability", capability, "suspicious", "device_attestation", observed, now),
        "verified_compromise": _signal("verified_compromise", device_verified, "verified", "device_attestation", observed, now),
        "recipient_bank_confirmed_mule": _signal("recipient_bank_confirmed_mule", mule, "verified", "core_banking", observed, now),
        "recipient_le_confirmed_case": _signal("recipient_le_confirmed_case", case, "verified", "core_banking", observed, now),
    }
    flags = {
        "simo_info_rule": _info_rule(facts, signals),
        "simo_with_context": _any_present(signals, load_policy()["simo_context"]),
        "merchant_verified": _tri(facts.merchant_verified),
    }
    return signals, flags, funds


def _funds_observed(funds: list[dict], now: datetime) -> datetime:
    if not funds:
        return now
    return min(_parse(item["at"]) for item in funds)


def _known_hit(value: float | None, hit: bool) -> str:
    if value is None:
        return "unknown"
    return "present" if hit else "absent"


def _any_present(signals: dict[str, Signal], names: list[str]) -> str:
    """Có mặt khi một tín hiệu đã quan sát là present. unknown không nâng mức."""
    if any(signals[name].status == "present" for name in names if name in signals):
        return "present"
    return "absent"


def _unusual(facts: Facts) -> str:
    """passthrough vượt ngưỡng và (nhiều người gửi hoặc tài khoản còn mới)."""
    passed = _known_hit(
        facts.passthrough_24h,
        facts.passthrough_24h is not None and facts.passthrough_24h > float(threshold("passthrough_24h_above")),
    )
    age = _known_hit(
        facts.payee_age_days,
        facts.payee_age_days is not None and facts.payee_age_days < float(threshold("recipient_age_days_below")),
    )
    senders = _known_hit(
        facts.distinct_senders_7d,
        facts.distinct_senders_7d is not None and facts.distinct_senders_7d > float(threshold("distinct_senders_7d_above")),
    )
    if age == "present" or senders == "present":
        branch = "present"
    elif age == "absent" and senders == "absent":
        branch = "absent"
    else:
        branch = "unknown"
    if passed == "absent" or branch == "absent":
        return "absent"
    if passed == "present" and branch == "present":
        return "present"
    return "unknown"


def _model(probability: float | None) -> str:
    if probability is None:
        return "unknown"
    return "present" if probability >= float(threshold("model_p_high")) else "absent"


def cost_tau(amount: float, segment: str) -> float:
    """τ(A) = c_FP / (c_FP + η·A), c_FP = scale × (50.000 + 0,002·A). Doanh nghiệp nhỏ ×3."""
    paid = 0.0 if amount != amount or amount < 0 else float(amount)
    scale = float(threshold("c_fp_sme_scale")) if segment == "doanh nghiệp nhỏ" else 1.0
    false_positive = (float(threshold("c_fp_fixed")) + float(threshold("c_fp_rate")) * paid) * scale
    return false_positive / (false_positive + float(threshold("cost_eta")) * paid)


def _model_cost(facts: Facts) -> str:
    if facts.p_fraud is None:
        return "unknown"
    return "present" if facts.p_fraud >= cost_tau(float(facts.amount), facts.segment) else "absent"


def _behaviour(facts: Facts) -> str:
    """Baseline thấp hoặc chưa có thì behaviour_rare là unknown, kể cả khi p đã tính."""
    block = load_policy().get("behavioural_baseline") or {}
    minimum = str(block.get("min_quality") or "medium")
    needed = int(block.get("min_sessions_for_level_2", threshold("behaviour_baseline_medium_sessions")))
    sessions = 0 if facts.same_task_sessions is None else int(facts.same_task_sessions)
    quality = _baseline(facts.same_task_sessions)
    if sessions < needed or BASELINE_RANK.get(quality, 0) < BASELINE_RANK.get(minimum, 2):
        return "unknown"
    if facts.behaviour_p_rarity is None:
        return "unknown"
    return "present" if facts.behaviour_p_rarity <= float(threshold("behaviour_p_rarity_at_most")) else "absent"


def _device(state: str | None) -> tuple[str, str, str]:
    if state == "active_capture":
        return "present", "absent", "absent"
    if state == "capability_detected":
        return "absent", "present", "absent"
    if state == "verified_compromise":
        return "absent", "absent", "present"
    if state == "none":
        return "absent", "absent", "absent"
    return "unknown", "unknown", "unknown"


def _verified(items: list[dict]) -> tuple[str, str]:
    found = {"recipient_bank_confirmed_mule": "absent", "recipient_le_confirmed_case": "absent"}
    for item in items:
        signal_id = str(item.get("signal_id") or "")
        if signal_id not in VERIFIED_SOURCES:
            continue
        source = str(item.get("source") or "")
        if source == "simo" or source not in VERIFIED_SOURCES[signal_id]:
            continue
        active = item.get("active", item.get("status") == "present")
        if item.get("status") == "present" and active is True:
            found[signal_id] = "present"
        elif item.get("status") == "unknown":
            if found[signal_id] != "present":
                found[signal_id] = "unknown"
    return found["recipient_bank_confirmed_mule"], found["recipient_le_confirmed_case"]


def _info_rule(facts: Facts, signals: dict[str, Signal]) -> str:
    if facts.simo_listed is not True:
        return "absent" if facts.simo_listed is False else "unknown"
    if facts.prior_payments is None or facts.first_days_before_listing is None:
        return "unknown"
    enough = facts.prior_payments >= int(threshold("simo_prior_payments"))
    early = facts.first_days_before_listing >= float(threshold("simo_first_days_before_listing"))
    if not enough or not early:
        return "absent"
    for name in load_policy()["manipulation_signals"]:
        signal = signals.get(name)
        if signal is None:
            continue
        if name == "behaviour_rare" and _baseline(facts.same_task_sessions) == "low":
            continue
        if signal.status == "present":
            return "absent"
    return "present"


def _status(name: str, signals: dict[str, Signal], flags: dict[str, str]) -> str:
    if name in signals:
        return signals[name].status
    return flags.get(name, "unknown")


def _near_limit(amount: int, limits: list[int]) -> bool:
    fraction = float(threshold("near_limit_below"))
    for limit in limits:
        if limit <= 0 or amount <= 0 or amount >= limit:
            continue
        if (limit - amount) <= limit * fraction:
            return True
    return False


def _point_limits(facts: Facts) -> list[int]:
    """Hạn một lệnh và mốc sinh trắc. Hạn ngày tính riêng, cộng số đã chuyển trong ngày."""
    found = [int(threshold("biometric_vnd"))]
    if facts.limit_per_txn:
        found.append(int(facts.limit_per_txn))
    return found


def _split_suspected(facts: Facts) -> bool:
    amount = int(facts.amount)
    if _near_limit(amount, _point_limits(facts)):
        return True
    if not facts.limit_per_day:
        return False
    spent = 0 if facts.spent_today is None else int(facts.spent_today)
    if spent < 0:
        spent = 0
    return _near_limit(amount + spent, [int(facts.limit_per_day)])


def _expire(doc: dict, now: datetime) -> None:
    hours = float(threshold("episode_hours"))
    if doc.get("expires_at"):
        if now >= _parse(doc["expires_at"]):
            doc["episode_id"] = ""
            doc["floor"] = 0
            doc["payee"] = ""
            doc["funds"] = []
            doc["scope"] = ""
            doc["opened_at"] = ""
            doc["expires_at"] = ""
    cutoff = now - timedelta(hours=hours)
    doc["tallies"] = [item for item in doc.get("tallies") or [] if _parse(item["at"]) >= cutoff]
    if doc.get("customer_until") and now >= _parse(doc["customer_until"]):
        doc["customer_floor"] = 0
        doc["customer_until"] = ""


def _joins(doc: dict, facts: Facts, signals: dict[str, Signal], funds: list[dict], now: datetime) -> bool:
    if not doc.get("episode_id") or not doc.get("expires_at"):
        return False
    if now >= _parse(doc["expires_at"]):
        return False
    if facts.payee and facts.payee == doc.get("payee"):
        return True
    if doc.get("scope") == "payee":
        return False
    if signals["beneficiary_is_new"].status == "present" or signals["recipient_unusual"].status == "present":
        return True
    return bool(set(_funds_keys(funds)) & set(doc.get("funds") or []))


def _match(rule: dict, signals: dict[str, Signal], flags: dict[str, str], baseline: str, fired: set[str]) -> tuple[bool, bool]:
    for name in rule.get("unless_fired") or []:
        if name in fired:
            return False, False
    needed = rule.get("baseline_at_least")
    if needed and BASELINE_RANK[baseline] < BASELINE_RANK[needed]:
        return False, False
    present_names = [name for name in (rule.get("all_present") or []) if not (rule.get("needs_call") and name == "has_call")]
    present_ok = all(_status(name, signals, flags) == "present" for name in present_names)
    absent_ok = all(_status(name, signals, flags) == "absent" for name in (rule.get("all_absent") or []))
    any_names = rule.get("any_present") or []
    any_ok = not any_names or any(_status(name, signals, flags) == "present" for name in any_names)
    if rule.get("needs_call") and _status("has_call", signals, flags) == "unknown":
        return False, present_ok and absent_ok and any_ok
    if rule.get("needs_call") and _status("has_call", signals, flags) != "present":
        return False, False
    return present_ok and absent_ok and any_ok, False


def _reasons(facts: Facts, rules: list[dict], signals: dict[str, Signal], funds: list[dict], behaviour=None, baseline: str = "none") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    fired = {rule["id"] for rule in rules}

    def add(code: str, text: str) -> None:
        if text and text not in [item[1] for item in rows]:
            rows.append((code, text))

    for rule in rules:
        if rule["id"] == "R_CALL_NEWPAYEE":
            add(contract_code("has_call", facts.call_state), CALL_NEWPAYEE_TEXT)
            continue
        names = list(rule.get("all_present") or []) + list(rule.get("any_present") or [])
        for name in names:
            if name == "recipient_simo_flag" and "R_SIMO_KNOWN" in fired:
                continue
            if not signals.get(name) or signals[name].status != "present":
                continue
            code = contract_code(name, facts.call_state)
            if name == "behaviour_rare":
                if BASELINE_RANK.get(baseline, 0) < BASELINE_RANK["medium"]:
                    continue
                phrase = ""
                if behaviour is not None:
                    phrase = next((item for item in getattr(behaviour, "reasons", []) if item), "")
                add(code, phrase or _reason_text(name, facts, funds))
                continue
            add(code, _reason_text(name, facts, funds))
        if rule["id"] == "R_SIMO_KNOWN":
            add("simo_info", SIMO_INFO_TEXT)
        if rule["id"] == "R_SIMO":
            add(contract_code("recipient_simo_flag"), SIMO_TEXT)
    return rows


def _call_text(facts: Facts) -> str:
    if facts.call_state == "ended_in_session":
        return "Cuộc gọi vừa kết thúc trong phiên này."
    return "Có cuộc gọi đang diễn ra trong phiên này."


def _reason_text(name: str, facts: Facts, funds: list[dict]) -> str:
    if name == "has_call":
        return _call_text(facts)
    if name == "recent_funds_event":
        sentences = []
        catalog = load_policy()["funds_types"]
        for item in funds:
            sentence = catalog.get(item["type"])
            if sentence and sentence not in sentences:
                sentences.append(sentence)
        return " ".join(sentences) if sentences else "Có tiền vừa vào từ sổ, khoản vay, hạn mức hoặc ứng thẻ."
    if name == "beneficiary_is_new":
        return "Chưa từng chuyển tới người nhận này."
    if name == "entry_paste_or_qr":
        if facts.task_type == "qr":
            return "Quét mã QR."
        return "Số tài khoản được dán."
    if name == "purpose_high_risk":
        return "Mục đích chuyển là yêu cầu của cơ quan hoặc phí để nhận tiền."
    if name == "recipient_unusual":
        return "Tài khoản nhận còn mới, có nhiều người chuyển tới, hoặc tiền đi qua rất nhanh."
    if name == "recipient_simo_flag":
        return SIMO_TEXT
    if name == "model_p_high":
        return "Mẫu chấm điểm cho lệnh này một điểm cao."
    if name == "model_p_cost":
        # Khớp purpose_amount_vnd của app. Dưới ngưỡng không nói "số tiền lớn".
        base = "Giao dịch khác thường so với lịch sử của bạn"
        if int(facts.amount) >= 10_000_000:
            return f"Số tiền lớn và {base[:1].lower()}{base[1:]}"
        return base
    if name == "behaviour_rare":
        return "Cách thao tác khác với những lần chuyển cùng loại trước đây."
    if name == "screen_capture_active":
        return "Màn hình đang bị ghi hoặc chiếu."
    if name == "device_capability":
        return "Máy có công cụ có thể xem hoặc điều khiển màn hình."
    if name == "verified_compromise":
        return "Thiết bị này đã được xác nhận bị chiếm quyền."
    if name == "recipient_bank_confirmed_mule":
        return "Ngân hàng đã xác nhận tài khoản nhận nằm trong hồ sơ đường dây."
    if name == "recipient_le_confirmed_case":
        return "Cơ quan điều tra đã xác nhận tài khoản nhận."
    return ""


def evaluate(facts: Facts, state: MemoryState | RedisState | None = None, behaviour=None) -> Outcome:
    started = datetime.now(ICT)
    clock = _when(facts)
    policy = load_policy()
    store = state if state is not None else MemoryState()
    doc = store.load(facts.sender or "_")
    _expire(doc, clock)
    signals, flags, funds = build_signals(facts, clock)
    baseline = _baseline(facts.same_task_sessions)
    split = _split_suspected(facts)
    joined = _joins(doc, facts, signals, funds, clock)
    fired: list[dict] = []
    fired_ids: set[str] = set()
    call_new = next(rule for rule in policy["rules"] if rule["id"] == "R_CALL_NEWPAYEE")
    for rule in policy["rules"]:
        if rule["id"] == "R_CALL_NEWPAYEE":
            continue
        matched, blocked = _match(rule, signals, flags, baseline, fired_ids)
        if blocked:
            log.info("%s not fired: unknown", rule["id"])
        if matched:
            fired.append(rule)
            fired_ids.add(rule["id"])
    rule_level = max((int(rule["level"]) for rule in fired), default=0)
    # Trần, không phải sàn: cuộc gọi và người nhận mới không đưa mức lên trên 1.
    matched, blocked = _match(call_new, signals, flags, baseline, fired_ids)
    if blocked:
        log.info("%s not fired: unknown", call_new["id"])
    if matched and rule_level < 2:
        fired.append(call_new)
        fired_ids.add(call_new["id"])
        rule_level = max(rule_level, 1)
    episode_floor = int(doc.get("floor") or 0) if joined else 0
    customer_floor = int(doc.get("customer_floor") or 0)
    user_floor = max(0, int(facts.user_level_floor or 0))
    own = max(rule_level, user_floor)
    level = max(own, episode_floor, customer_floor)
    in_episode = joined
    if own >= 2 and not joined:
        broad = _any_present(signals, policy["episode_context"]) == "present"
        doc["episode_id"] = f"ep-{facts.sender[:12]}-{int(clock.timestamp() * 1000)}-{facts.payee[:8]}"
        doc["opened_at"] = _stamp(clock)
        doc["expires_at"] = _stamp(clock + timedelta(hours=float(threshold("episode_hours"))))
        doc["payee"] = facts.payee
        doc["funds"] = _funds_keys(funds) if broad else []
        doc["scope"] = "broad" if broad else "payee"
        doc["floor"] = level
        in_episode = True
    elif joined:
        doc["floor"] = max(int(doc.get("floor") or 0), level)
        doc["funds"] = sorted(set(doc.get("funds") or []) | set(_funds_keys(funds)))
    if any(rule.get("customer_level") for rule in fired):
        doc["customer_floor"] = max(int(doc.get("customer_floor") or 0), 3)
        doc["customer_until"] = _stamp(clock + timedelta(hours=float(threshold("customer_state_hours"))))
    if split or in_episode:
        doc.setdefault("tallies", []).append({"at": _stamp(clock), "amount": int(facts.amount)})
    cumulative = sum(int(item["amount"]) for item in doc.get("tallies") or [])
    if doc.get("episode_id") or doc.get("tallies") or doc.get("customer_floor"):
        store.save(facts.sender or "_", doc)
    if level >= 3:
        hold = clock + timedelta(hours=float(threshold("hold_level_3_hours")))
        funds_status = "held"
    elif level >= 2:
        hold = clock + timedelta(minutes=float(threshold("hold_level_2_minutes")))
        funds_status = "held"
    else:
        hold = None
        funds_status = "released"
    elapsed_ms = (datetime.now(ICT) - started).total_seconds() * 1000
    _note_latency(elapsed_ms)
    ordered = [signals[name] for name in SIGNAL_ORDER]
    reason_rows = _reasons(facts, fired, signals, funds, behaviour, baseline)
    reasons = [text for _, text in reason_rows]
    reason_codes = [code for code, _ in reason_rows]
    behaviour_score = None
    behaviour_p = facts.behaviour_p_rarity
    if behaviour is not None:
        behaviour_score = behaviour.score
        behaviour_p = behaviour.p_rarity
    return Outcome(
        level=level,
        rules_fired=[rule["id"] for rule in fired],
        signals=ordered,
        reasons_vi=reasons,
        reason_codes=reason_codes,
        baseline_quality=baseline,
        episode_id=doc["episode_id"] if in_episode else "",
        intervention=INTERVENTION[level],
        extra_auth=any(rule.get("extra_auth") for rule in fired),
        split_suspected=split,
        cumulative_amount=cumulative,
        funds_status=funds_status,
        hold_until=hold,
        score_ms=elapsed_ms,
        behaviour_score=behaviour_score,
        behaviour_p_rarity=behaviour_p,
    )


def _opt_float(row: dict, name: str) -> float | None:
    if name not in row or row.get(name) is None:
        return None
    try:
        number = float(row[name])
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _segment(row: dict) -> str:
    value = row.get("segment")
    if value is None:
        return "thường"
    try:
        if float(value) != float(value):
            return "thường"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or "thường"


def _body_int(body: dict, name: str) -> int | None:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _opt_bool(row: dict, name: str) -> bool | None:
    if name not in row or row.get(name) is None:
        return None
    try:
        return bool(int(row[name]))
    except (TypeError, ValueError):
        return None


def facts_from_row(body: dict, row: dict, probability: float, decided: datetime, limit_per_txn: int | None) -> Facts:
    call = body.get("call") if isinstance(body.get("call"), dict) else None
    device = body.get("device") if isinstance(body.get("device"), dict) else None
    if "recent_funds_events" in body and isinstance(body.get("recent_funds_events"), list):
        funds = body["recent_funds_events"]
    else:
        funds = None
    verified = body.get("signals") if isinstance(body.get("signals"), list) else []
    body_txn = _body_int(body, "limit_per_txn")
    limit = body_txn if body_txn is not None else (limit_per_txn if limit_per_txn else None)
    day = _body_int(body, "limit_per_day")
    spent = _body_int(body, "spent_today")
    return Facts(
        sender=str(body.get("sender_hash") or ""),
        payee=str(body.get("payee_hash") or ""),
        amount=int(body.get("amount") or 0),
        ts=decided,
        call_state=None if call is None else call.get("state"),
        task_type=body.get("task_type"),
        transfer_purpose=body.get("transfer_purpose"),
        device_state=None if device is None else device.get("state"),
        funds_events=funds,
        new_payee=_opt_bool(row, "new_payee"),
        payee_age_days=_opt_float(row, "payee_account_age_days"),
        distinct_senders_7d=_opt_float(row, "distinct_senders_7d"),
        passthrough_24h=_opt_float(row, "passthrough_24h"),
        merchant_verified=_opt_bool(row, "merchant_verified"),
        simo_listed=_opt_bool(row, "simo_listed"),
        prior_payments=None if _opt_float(row, "payee_transfers_before_listing") is None else int(_opt_float(row, "payee_transfers_before_listing")),
        first_days_before_listing=_opt_float(row, "payee_first_days_before_listing"),
        p_fraud=None if probability is None else float(probability),
        segment=_segment(row),
        behaviour_p_rarity=None,
        same_task_sessions=None,
        limit_per_txn=limit,
        limit_per_day=day,
        spent_today=spent,
        verified=verified,
        user_level_floor=0,
    )

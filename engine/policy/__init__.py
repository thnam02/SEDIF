"""Chính sách mức và phản hồi. Không đọc tệp, không mở mạng."""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = ["none", "banner", "medium", "high"]
RECOVERY_S = 24 * 60 * 60


@dataclass
class Respond:
    decision_id: str
    who_guides_answer: str | bool | None = None
    guardian_confirm: bool | None = None
    hung_up: bool | None = None
    cancel: bool | None = None


def guiding(answer) -> bool:
    if answer is None or answer is False:
        return False
    if isinstance(answer, str) and answer.strip().lower() in {"", "không", "khong", "no", "false", "0"}:
        return False
    return True


def raise_level(level: str, answer) -> str:
    """who_guides chỉ được nâng mức, không bao giờ hạ."""
    if not guiding(answer):
        return level
    return LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]


def actions_for(level: str, segment: str, has_guardian: bool, *, consented: bool | None = None) -> list[str]:
    """guardian_confirm chỉ khi có bản đồng ý đã lưu. consented=None giữ lối cũ cho /respond."""
    guardian = has_guardian if consented is None else consented
    if level == "none":
        return ["allow"]
    if level == "info":
        return ["info"]
    if level == "banner":
        return ["g1_banner"]
    if level == "medium" and segment == "cao tuổi" and guardian:
        return ["pause_and_verify", "guardian_confirm"]
    if level == "medium":
        return ["pause_and_verify"]
    return ["hangup_required", "recovery_window"]


def apply_respond(record: dict, body: Respond, now: float) -> dict:
    level = raise_level(record["level"], body.who_guides_answer)
    if LEVELS.index(level) < LEVELS.index(record["level"]):
        level = record["level"]
    record["level"] = level
    record["actions"] = actions_for(level, record["segment"], record["has_guardian"])
    status = record.get("status", "open")
    if record["has_guardian"] and level == "medium" and not record.get("guardian_confirmed"):
        status = "waiting_guardian"
    elif status == "waiting_guardian":
        status = "open"
    if body.guardian_confirm is True and record["has_guardian"] and level == "medium":
        record["guardian_confirmed"] = True
        status = "released"
    if body.hung_up is True:
        record["hung_up"] = True
        if level == "high":
            status = "hung_up"
    if body.cancel is True:
        if now - float(record["decided_at"]) <= RECOVERY_S:
            status = "cancelled"
        else:
            record["cancel_rejected"] = True
    record["status"] = status
    record["respond"] = {
        "who_guides_answer": body.who_guides_answer,
        "guardian_confirm": body.guardian_confirm,
        "hung_up": body.hung_up,
        "cancel": body.cancel,
        "status": status,
    }
    return record

"""FastAPI /decide và /respond. Chỉ nạp gói model-v2.0, không train lại."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.reasons import reasons_from_contrib
from api.runtime import BadRequest, Runtime, UnknownSession

LEVELS = ["none", "banner", "medium", "high"]
GROUPS = ("g1", "g2", "g3", "g4")
FAMILY_NAMES = ("call", "entry", "payee", "funding")
RECOVERY_S = 24 * 60 * 60
MODEL_VERSION = "model-v2.0"


class DeviceIn(BaseModel):
    in_call: bool | None = None
    call_seconds_since_open: float | None = None
    call_direction: str | None = None
    call_ended_in_session: bool | None = None
    acct_input: str | None = None
    app_switches: int | None = None


class DecideIn(BaseModel):
    decision_id: str | None = None
    profile: str
    sender_account: str
    payee_account: str
    amount: float
    ts: str
    device: DeviceIn | None = None


class RespondIn(BaseModel):
    decision_id: str
    who_guides_answer: str | bool | None = None
    guardian_confirm: bool | None = None
    hung_up: bool | None = None
    cancel: bool | None = None


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.records: dict[str, dict] = {}

    def write(self, record: dict, replace: bool) -> None:
        with self.lock:
            self.records[record["decision_id"]] = record
            if not replace:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                return
            lines = [json.dumps(item, ensure_ascii=False) for item in self.records.values()]
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def account_hash(account: str) -> str:
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()


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


def actions_for(level: str, segment: str, has_guardian: bool) -> list[str]:
    if level == "none":
        return ["allow"]
    if level == "banner":
        return ["g1_banner"]
    if level == "medium" and segment == "cao tuổi" and has_guardian:
        return ["pause_and_verify", "guardian_confirm"]
    if level == "medium":
        return ["pause_and_verify"]
    return ["hangup_required", "recovery_window"]


def apply_respond(record: dict, body: RespondIn, now: float) -> dict:
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


def audit_path() -> Path:
    raw = os.environ.get("FIDES_AUDIT")
    if raw:
        return Path(raw)
    return ROOT / "out" / "audit" / "decisions.jsonl"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = Runtime.load()
    app.state.audit = AuditLog(audit_path())
    yield


app = FastAPI(title="FIDES", version="v2.1", lifespan=lifespan)


@app.post("/decide")
def decide(body: DecideIn) -> dict:
    runtime: Runtime = app.state.runtime
    if body.profile not in {"basic", "standard", "advanced"}:
        raise HTTPException(400, "profile phải là basic, standard hoặc advanced")
    profile = "basic" if body.device is None else body.profile
    session_id = runtime.find_session(body.sender_account, body.ts)
    if session_id is None:
        raise HTTPException(404, "không có phiên của tài khoản tại mốc thời gian này")
    try:
        scored = runtime.score_session(session_id, body.ts, profile)
    except UnknownSession:
        raise HTTPException(404, "không có phiên") from None
    except BadRequest as exc:
        raise HTTPException(400, str(exc)) from None
    decision_id = body.decision_id or str(uuid.uuid4())
    actions = actions_for(scored["level"], scored["segment"], scored["has_guardian"])
    flags = [scored["call"], scored["entry"], scored["payee"], scored["funding"]]
    families_hit = [group for group, flag in zip(GROUPS, flags) if flag]
    if scored["anomaly"]:
        families_hit.append("anomaly")
    status = "waiting_guardian" if scored["has_guardian"] and scored["level"] == "medium" else "open"
    record = {
        "decision_id": decision_id,
        "ts": body.ts,
        "decided_at": time.time(),
        "model_version": MODEL_VERSION,
        "profile": profile,
        "sender_hash": account_hash(body.sender_account),
        "payee_hash": account_hash(body.payee_account),
        "feature_vector": scored["feature_vector"],
        "p": scored["p_fraud"],
        "level": scored["level"],
        "actions": actions,
        "respond": None,
        "segment": scored["segment"],
        "has_guardian": scored["has_guardian"],
        "guardian_confirmed": False,
        "status": status,
        "session_id": scored["session_id"],
    }
    app.state.audit.write(record, replace=False)
    return {
        "decision_id": decision_id,
        "level": scored["level"],
        "actions": actions,
        "p_fraud": scored["p_fraud"],
        "group_scores": {group: float(flag) for group, flag in zip(GROUPS, flags)},
        "families_hit": families_hit,
        "reasons": reasons_from_contrib(runtime.columns, scored["contrib"]),
        "missing_signals": scored["missing_signals"],
        "profile": profile,
        "model_version": MODEL_VERSION,
        "status": status,
    }


@app.post("/respond")
def respond(body: RespondIn) -> dict:
    audit: AuditLog = app.state.audit
    record = audit.records.get(body.decision_id)
    if record is None:
        raise HTTPException(404, "không có quyết định")
    before = record["level"]
    apply_respond(record, body, time.time())
    if LEVELS.index(record["level"]) < LEVELS.index(before):
        record["level"] = before
    audit.write(record, replace=True)
    return {
        "decision_id": record["decision_id"],
        "level": record["level"],
        "actions": record["actions"],
        "status": record["status"],
        "model_version": MODEL_VERSION,
    }


def main() -> None:
    import uvicorn

    uvicorn.run("api.service:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), factory=False)


if __name__ == "__main__":
    main()

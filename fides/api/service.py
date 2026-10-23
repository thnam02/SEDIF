"""FastAPI /decide và /respond. Chỉ nạp gói model-v2.0, không train lại."""

from __future__ import annotations

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
REPO = ROOT.parent
for path in (ROOT, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.reasons import reasons_from_contrib
from api.runtime import BadRequest, Runtime, UnknownSession
from engine.common.hashing import account_hash, require_key
from engine.policy import LEVELS, actions_for, apply_respond, raise_level

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


def audit_path() -> Path:
    raw = os.environ.get("FIDES_AUDIT")
    if raw:
        return Path(raw)
    return ROOT / "out" / "audit" / "decisions.jsonl"


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_key()
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

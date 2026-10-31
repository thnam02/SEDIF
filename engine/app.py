"""Engine: /events, /decide, /respond, /decisions. Khoá HMAC và API key thiếu thì không khởi động."""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from engine.audit import (
    audit_conn,
    decision_payload,
    ensure_audit,
    insert_decision,
    insert_response,
    record_choice,
    list_decisions,
    load_state,
    reset_countdown,
)
from engine.common.hashing import require_key
from engine.decide import (
    apply_contract,
    gated_families,
    iso,
    missing_signals,
    parse_ts,
    profile_missing,
    reasons_for,
    to_us,
)
from engine.events import connect, ensure_schema, insert_event
from engine.features.behaviour import assess, remember
from engine.features.online import OnlineStore
from engine.features.stream import TRANSFER_TYPES
from engine.model import Scorer
from engine.review import confirm as confirm_review
from engine.review import consider as consider_review
from engine.review import queue as review_queue
from engine.policy import actions_for
from engine.policy.v24 import STRING_LEVEL, RedisState, evaluate, facts_from_row, signals_present

log = logging.getLogger("engine")
NAME = os.environ.get("SERVICE_NAME", "engine")
RESET_ID = ""
_RESET_LOADED = False
HASH_LEN = 64
TYPES = {"txn", "account_change", "device_change", "product", "complaint", "simo_listing"}
STREAM = {"account", "session", "device_link", "payee_add", "session_event", "device_signal", "txn", "product", "account_change", "simo_listing"}
PROFILES = {"basic", "standard", "advanced"}

STORE: OnlineStore | None = None
MODEL: Scorer | None = None
SCORE_LOCK = threading.Lock()


def require_api_key() -> str:
    raw = os.environ.get("FIDES_API_KEY", "")
    if not raw:
        raise RuntimeError("FIDES_API_KEY thiếu, không khởi động")
    return raw


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _bad(handler: BaseHTTPRequestHandler, status: int, detail: str) -> None:
    _json(handler, status, {"detail": detail})


def _hash_ok(value) -> bool:
    return isinstance(value, str) and len(value) == HASH_LEN and all(ch in "0123456789abcdef" for ch in value)


def current_reset_id() -> str:
    global RESET_ID, _RESET_LOADED
    if _RESET_LOADED:
        return RESET_ID
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_state (
                id integer PRIMARY KEY,
                reset_id text NOT NULL
            )
            """
        )
        row = conn.execute("SELECT reset_id FROM demo_state WHERE id = 1").fetchone()
        RESET_ID = str(row[0]) if row else ""
        conn.commit()
    _RESET_LOADED = True
    return RESET_ID


def store_reset(value: str) -> None:
    global RESET_ID, _RESET_LOADED
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_state (
                id integer PRIMARY KEY,
                reset_id text NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO demo_state (id, reset_id) VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET reset_id = EXCLUDED.reset_id
            """,
            (value,),
        )
        conn.commit()
    RESET_ID = value
    _RESET_LOADED = True


def _same_reset(handler: BaseHTTPRequestHandler) -> bool:
    sent = handler.headers.get("X-Reset-Id")
    if sent is None:
        return True
    if sent != current_reset_id():
        log.warning("ledger_mismatch")
        _bad(handler, 409, "ledger_mismatch")
        return False
    return True


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    sent = handler.headers.get("X-API-Key", "")
    if not sent or not hmac.compare_digest(sent, require_api_key()):
        _bad(handler, 401, "sai API key")
        return False
    return True


def _read_json(handler: BaseHTTPRequestHandler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _valid(event: dict) -> str | None:
    if not isinstance(event, dict):
        return "body phải là object"
    if event.get("type") not in TYPES:
        return "type không hợp lệ"
    if not event.get("event_id") or not isinstance(event["event_id"], str):
        return "thiếu event_id"
    if not event.get("ts"):
        return "thiếu ts"
    if not _hash_ok(event.get("account_hash")):
        return "account_hash phải là HMAC 64 ký tự hex"
    counterparty = event.get("counterparty_hash")
    if counterparty is not None and not _hash_ok(counterparty):
        return "counterparty_hash phải là HMAC 64 ký tự hex"
    if event["type"] == "txn" and not event.get("tx_type"):
        return "txn thiếu tx_type"
    if event["type"] == "account_change" and event.get("change_type") not in {"limit_raise", "phone_change"}:
        return "account_change thiếu change_type"
    if event["type"] == "device_change" and event.get("change_type") != "new_device":
        return "device_change phải là new_device"
    if event["type"] == "product" and not event.get("product_type"):
        return "product thiếu product_type"
    if event["type"] == "simo_listing" and (not event.get("listed_at") or not event.get("source")):
        return "simo_listing thiếu listed_at hoặc source"
    return None


def _stream_event(event: dict) -> bool:
    return isinstance(event, dict) and event.get("type") in STREAM and ("account" in event or event.get("type") in {"session", "session_event", "device_signal", "payee_add"})


def _ledger_stream(event: dict) -> dict | None:
    kind = event.get("type")
    if kind not in TYPES or "account_hash" not in event:
        return None
    stamp = event.get("ts")
    us = to_us(parse_ts(stamp)) if isinstance(stamp, str) else int(stamp)
    if kind == "txn" or kind == "product":
        tx = event.get("tx_type") or event.get("product_type")
        return {
            "type": "txn",
            "ts": us,
            "account": event["account_hash"],
            "payee": event.get("counterparty_hash"),
            "known_payee": bool(event.get("counterparty_hash")),
            "amount": int(event.get("amount") or 0),
            "balance": int(event.get("balance") or 0),
            "session": event.get("session_id") or "",
            "tx_type": tx,
            "xfer": tx in TRANSFER_TYPES,
        }
    if kind == "account_change":
        return {"type": "account_change", "ts": us, "account": event["account_hash"], "change_type": event.get("change_type")}
    if kind == "simo_listing":
        return {"type": "simo_listing", "ts": us, "account": event["account_hash"]}
    return None


def ingest(event: dict) -> bool:
    """Cùng đường POST /events: cập nhật feature store, rồi ghi sổ nếu là event sổ cái."""
    stream = event if _stream_event(event) else _ledger_stream(event)
    if STORE is not None and stream is not None:
        STORE.update(stream)
        STORE.flush()
        if stream.get("type") == "txn":
            from datetime import datetime

            from engine.decide import ICT

            raw_ts = event.get("ts")
            when = parse_ts(raw_ts) if isinstance(raw_ts, str) else datetime.fromtimestamp(int(stream["ts"]) / 1_000_000, ICT)
            consider_review(STORE, stream, when)
        if event.get("type") == "device_signal" and event.get("in_call") == "1":
            account = STORE.session_account.get(event.get("session", ""))
            if account:
                stamp = parse_ts(event["ts"]) if isinstance(event.get("ts"), str) else None
                if stamp is None and isinstance(event.get("ts"), int):
                    from datetime import datetime
                    from engine.decide import ICT

                    stamp = datetime.fromtimestamp(event["ts"] / 1_000_000, ICT)
                if stamp is not None:
                    with audit_conn() as conn:
                        reset_countdown(conn, account, stamp + timedelta(minutes=10), stamp)
    if event.get("type") in TYPES and "account_hash" in event:
        with connect() as conn:
            return insert_event(conn, event)
    return True


def _features(row: dict, columns: list[str]) -> dict:
    out = {}
    for name in columns:
        value = row.get(name)
        if hasattr(value, "item"):
            value = value.item()
        out[name] = None if value is None else value
    return out


def decide(body: dict) -> tuple[int, dict]:
    if MODEL is None or STORE is None:
        return 503, {"detail": "model chưa nạp"}
    for key in ("decision_id", "profile", "sender_hash", "payee_hash", "amount", "ts"):
        if key not in body:
            return 400, {"detail": f"thiếu {key}"}
    if body["profile"] not in PROFILES:
        return 400, {"detail": "profile không hợp lệ"}
    if not _hash_ok(body["sender_hash"]) or not _hash_ok(body["payee_hash"]):
        return 400, {"detail": "hash phải là HMAC 64 ký tự hex"}
    profile = body["profile"]
    if "device" not in body or body.get("device") is None:
        profile = "basic"
    decided = parse_ts(body["ts"])
    session_id = body.get("session_id") or STORE.session_at(body["sender_hash"], to_us(decided))
    if not session_id:
        return 404, {"detail": "không có phiên tại ts"}
    pending = None
    if body.get("score_request"):
        pending = {
            "ts": to_us(decided),
            "payee": body["payee_hash"],
            "amount": int(body["amount"]),
            "balance": int(body.get("balance") or 0),
        }
    row = STORE.get(body["sender_hash"], body["payee_hash"], to_us(decided), str(session_id), pending)
    if pending and row.get("new_payee"):
        row["payee_family"] = 1
    if profile == "basic":
        for name in profile_missing(profile):
            row[name] = None
        for name in ("payee_account_age_days", "distinct_senders_7d", "passthrough_24h", "device_holder_count"):
            row[f"{name}_missing"] = 1
    with SCORE_LOCK:
        probability = MODEL.score(row)
    families = gated_families(row)
    segment = str(row.get("segment") or "thường")
    limit = int(STORE.limit_of.get(body["sender_hash"]) or 0)
    behaviour = assess(
        STORE.redis,
        str(body.get("user_hash") or body["sender_hash"]),
        body.get("task_type"),
        body.get("behaviour_features"),
        decided,
        experiment=isinstance(body.get("experiment"), dict),
    )
    facts = facts_from_row(body, row, probability, decided, limit or None)
    if behaviour is not None:
        facts.same_task_sessions = behaviour.n
        facts.behaviour_p_rarity = behaviour.p_rarity if behaviour.usable else None
    outcome = evaluate(facts, RedisState(STORE.redis), behaviour)
    level_name = STRING_LEVEL[outcome.level]
    guardian = bool(int(row.get("has_guardian") or 0))
    actions = actions_for(level_name, segment, guardian)
    present = signals_present(outcome, facts.call_state)
    reasons = outcome.reason_rows()[:3] or reasons_for(row, families)
    funds, hold = outcome.funds_status, outcome.hold_until
    payload = {
        "decision_id": body["decision_id"],
        "level": outcome.level,
        "rules_fired": outcome.rules_fired,
        "evidence_card": outcome.evidence_card,
        "intervention": outcome.intervention,
        "extra_auth": outcome.extra_auth,
        "actions": actions,
        "p_fraud": probability,
        "signals_present": present,
        "reasons": reasons,
        "missing_signals": profile_missing(profile) if profile == "basic" else missing_signals(row, MODEL.columns),
        "profile": profile,
        "model_version": MODEL.version,
        "hold_until": iso(hold),
        "countdown_until": None,
        "funds_status": funds,
    }
    stored = {
        "decision_id": body["decision_id"],
        "ts": decided,
        "model_version": MODEL.version,
        "profile": profile,
        "sender_hash": body["sender_hash"],
        "payee_hash": body["payee_hash"],
        "features": _features(row, MODEL.columns),
        "p": probability,
        "level": level_name,
        "actions": actions,
        "families_hit": present,
        "reasons": reasons,
        "funds_status": funds,
        "hold_until": hold,
        "countdown_until": None,
        "segment": segment,
        "has_guardian": guardian,
        "payload": payload,
    }
    with audit_conn() as conn:
        if not insert_decision(conn, stored):
            return 409, {"detail": "decision_id đã có"}
    if behaviour is not None:
        remember(STORE.redis, behaviour, outcome.level)
    return 200, payload


def respond(body: dict) -> tuple[int, dict]:
    if not body.get("decision_id"):
        return 400, {"detail": "thiếu decision_id"}
    if body.get("ts"):
        now = parse_ts(body["ts"])
    else:
        from datetime import datetime

        from engine.decide import ICT

        now = datetime.now(ICT)
    with audit_conn() as conn:
        state = load_state(conn, body["decision_id"])
        if state is None:
            return 404, {"detail": "không có decision_id"}
        updated = apply_contract(state, body, now)
        insert_response(
            conn,
            {
                "decision_id": body["decision_id"],
                "ts": now,
                "level": updated["level"],
                "funds_status": updated["funds_status"],
                "gate": updated["gate"],
                "hold_until": updated["hold_until"],
                "countdown_until": updated["countdown_until"],
                "body": body,
            },
        )
    return 200, {
        "decision_id": body["decision_id"],
        "level": updated["level"],
        "funds_status": updated["funds_status"],
        "gate": updated["gate"],
        "hold_until": iso(updated["hold_until"]),
        "countdown_until": iso(updated["countdown_until"]),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json(self, 200, {"status": "ok", "service": NAME, "reset_id": current_reset_id()})
            return
        if not _authorized(self):
            return
        if parsed.path == "/decisions":
            query = parse_qs(parsed.query)
            level = query.get("level", [None])[0]
            raw_limit = query.get("limit", ["50"])[0]
            limit = min(max(int(raw_limit or 50), 1), 200)
            cursor = query.get("cursor", [None])[0]
            cursor_ts = cursor_id = None
            if cursor:
                cursor_ts, cursor_id = parse_ts(cursor.split("|", 1)[0]), cursor.split("|", 1)[1]
            from_ts = parse_ts(query["from_ts"][0]) if query.get("from_ts") else None
            to_ts = parse_ts(query["to_ts"][0]) if query.get("to_ts") else None
            with audit_conn() as conn:
                rows = list_decisions(conn, level, limit + 1, cursor_ts, cursor_id, from_ts, to_ts)
            page = rows[:limit]
            nxt = None
            if len(rows) > limit and page:
                nxt = f"{iso(page[-1]['ts'])}|{page[-1]['decision_id']}"
            _json(
                self,
                200,
                {
                    "decisions": [
                        {
                            "decision_id": row["decision_id"],
                            "ts": iso(row["ts"]),
                            "level": row["level"],
                            "funds_status": row["funds_status"],
                            "hold_until": iso(row["hold_until"]),
                            "countdown_until": iso(row["countdown_until"]),
                        }
                        for row in page
                    ],
                    "next_cursor": nxt,
                },
            )
            return
        if parsed.path == "/review-queue":
            with connect() as conn:
                rows = review_queue(conn)
            _json(self, 200, {"reviews": [{**row, "ts": iso(row["ts"])} for row in rows]})
            return
        if parsed.path.startswith("/decisions/"):
            decision_id = parsed.path.removeprefix("/decisions/")
            with audit_conn() as conn:
                payload = decision_payload(conn, decision_id)
            if payload is None:
                _bad(self, 404, "không có quyết định")
                return
            _json(self, 200, payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        try:
            self._post()
        except Exception as exc:
            _bad(self, 500, str(exc))

    def _post(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/review/") and path.endswith("/confirm"):
            if not _authorized(self):
                return
            review_id = path.removeprefix("/review/").removesuffix("/confirm")
            with connect() as conn:
                result = confirm_review(conn, review_id)
            if result is None:
                _bad(self, 404, "không có review")
                return
            _json(self, 200, result)
            return
        if path == "/demo/reset":
            if not _authorized(self):
                return
            body = _read_json(self) or {}
            if not body.get("reset_id"):
                _bad(self, 400, "thiếu reset_id")
                return
            if STORE is not None:
                STORE.forget_runtime()
            store_reset(str(body["reset_id"]))
            _json(self, 200, {"status": "ok", "reset_id": current_reset_id()})
            return
        if path.startswith("/decisions/") and path.endswith("/choice"):
            if not _authorized(self):
                return
            decision_id = path[len("/decisions/") : -len("/choice")].strip("/")
            body = _read_json(self)
            if body is None:
                _bad(self, 400, "JSON không đọc được")
                return
            choice = body.get("choice")
            response_ms = body.get("response_ms")
            if choice not in {"pause", "cancel", "continue"} or not isinstance(response_ms, int) or isinstance(response_ms, bool) or response_ms < 0:
                _bad(self, 400, "choice hoặc response_ms không hợp lệ")
                return
            with audit_conn() as conn:
                logged = record_choice(conn, decision_id, choice, response_ms)
            if not logged:
                _bad(self, 404, "không có quyết định")
                return
            _json(self, 200, {"decision_id": decision_id, "choice": choice, "logged": True})
            return
        if path not in {"/events", "/decide", "/respond"}:
            self.send_response(404)
            self.end_headers()
            return
        if not _authorized(self):
            return
        if not _same_reset(self):
            return
        event = _read_json(self)
        if event is None:
            _bad(self, 400, "JSON không đọc được")
            return
        if path == "/events":
            if _stream_event(event):
                ingest(event)
                _json(self, 201, {"event_id": event.get("event_id"), "stored": True})
                return
            reason = _valid(event)
            if reason:
                _bad(self, 400, reason)
                return
            stored = ingest(event)
            _json(self, 201 if stored else 200, {"event_id": event["event_id"], "stored": stored})
            return
        status, payload = decide(event) if path == "/decide" else respond(event)
        _json(self, status, payload)

    def log_message(self, fmt, *args):
        return


def main() -> None:
    global STORE, MODEL
    require_key()
    require_api_key()
    MODEL = Scorer.load()
    STORE = OnlineStore(flush=False)
    with connect() as conn:
        ensure_schema(conn)
    with audit_conn() as conn:
        ensure_audit(conn)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()

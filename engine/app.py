"""Engine: /health và POST /events. Khoá HMAC và API key thiếu thì không khởi động."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine.common.hashing import require_key
from engine.events import connect, ensure_schema, insert_event

NAME = os.environ.get("SERVICE_NAME", "engine")
HASH_LEN = 64
TYPES = {"txn", "account_change", "device_change", "product", "complaint", "simo_listing"}


def require_api_key() -> str:
    raw = os.environ.get("FIDES_API_KEY", "")
    if not raw:
        raise RuntimeError("FIDES_API_KEY thiếu, không khởi động")
    return raw


def _bad(handler: BaseHTTPRequestHandler, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _hash_ok(value) -> bool:
    return isinstance(value, str) and len(value) == HASH_LEN and all(ch in "0123456789abcdef" for ch in value)


def _valid(event: dict) -> str | None:
    if not isinstance(event, dict):
        return "body phải là object"
    if not event.get("event_id") or not event.get("ts") or not event.get("type"):
        return "thiếu event_id, type hoặc ts"
    if event["type"] not in TYPES:
        return "type không có trong hợp đồng"
    if not _hash_ok(event.get("account_hash")):
        return "account_hash phải là HMAC-SHA256 hex"
    counterparty = event.get("counterparty_hash")
    if counterparty is not None and not _hash_ok(counterparty):
        return "counterparty_hash phải là HMAC-SHA256 hex"
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"status": "ok", "service": NAME}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/events":
            self.send_response(404)
            self.end_headers()
            return
        sent = self.headers.get("X-API-Key", "")
        if not sent or not hmac.compare_digest(sent, require_api_key()):
            _bad(self, 401, "sai API key")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            event = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            _bad(self, 400, "JSON không đọc được")
            return
        reason = _valid(event)
        if reason:
            _bad(self, 400, reason)
            return
        with connect() as conn:
            stored = insert_event(conn, event)
        body = json.dumps({"event_id": event["event_id"], "stored": stored}).encode()
        self.send_response(201 if stored else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main() -> None:
    require_key()
    require_api_key()
    with connect() as conn:
        ensure_schema(conn)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()

"""GET /decisions phân trang theo limit, cursor, from_ts, to_ts và level."""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
os.environ.setdefault("FIDES_API_KEY", "fides-demo-key")
os.environ.setdefault("DATABASE_URL", "postgresql://hainamtran@localhost:5432/fides_w1")

from engine.app import Handler
from engine.audit import audit_conn, ensure_audit, insert_decision
from engine.common.hashing import reset_key_cache

ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def _post_get(port: int, path: str) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"X-API-Key": os.environ["FIDES_API_KEY"]})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def test_decisions_are_paged():
    reset_key_cache()
    with audit_conn() as conn:
        ensure_audit(conn)
        for index, level in enumerate(("none", "banner", "high")):
            stamp = datetime(2031, 1, 1, 0, index, tzinfo=ICT)
            insert_decision(
                conn,
                {
                    "decision_id": f"page-{index}",
                    "ts": stamp,
                    "model_version": "model-v2.1",
                    "profile": "advanced",
                    "sender_hash": "ab" * 32,
                    "payee_hash": "cd" * 32,
                    "features": {},
                    "p": 0.1,
                    "level": level,
                    "actions": ["allow"],
                    "families_hit": [],
                    "reasons": [],
                    "funds_status": "released",
                    "hold_until": None,
                    "countdown_until": None,
                    "segment": "thường",
                    "has_guardian": False,
                    "payload": {"decision_id": f"page-{index}", "level": level},
                },
            )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    first = _post_get(port, "/decisions?from_ts=2031-01-01T00:00:00%2B07:00&to_ts=2031-01-02T00:00:00%2B07:00&limit=2")
    assert [row["decision_id"] for row in first["decisions"]] == ["page-0", "page-1"]
    assert first["next_cursor"]
    second = _post_get(port, "/decisions?from_ts=2031-01-01T00:00:00%2B07:00&to_ts=2031-01-02T00:00:00%2B07:00&limit=2&cursor=" + urllib.parse.quote(first["next_cursor"]))
    assert [row["decision_id"] for row in second["decisions"]] == ["page-2"]
    assert second["next_cursor"] is None
    high = _post_get(port, "/decisions?from_ts=2031-01-01T00:00:00%2B07:00&level=high")
    assert [row["decision_id"] for row in high["decisions"]] == ["page-2"]
    server.shutdown()

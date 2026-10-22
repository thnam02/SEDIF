"""Máy chủ HTTP cho POST /decide và POST /respond."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.runtime import BadRequest, Runtime, UnknownSession


class Handler(BaseHTTPRequestHandler):
    runtime: Runtime

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, {"error": "JSON không đọc được"})
            return
        try:
            if self.path == "/decide":
                if "session_id" not in body:
                    raise BadRequest("thiếu session_id")
                payload = self.runtime.decide(str(body["session_id"]), body.get("as_of"))
            elif self.path == "/respond":
                payload = self.runtime.respond(body)
            else:
                self._send(404, {"error": "không có đường này"})
                return
        except UnknownSession:
            self._send(404, {"error": "không có phiên"})
            return
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, payload)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    Handler.runtime = Runtime.load()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"model-v2.0 đang nghe 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

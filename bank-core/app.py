"""Bank-core. /health, và cùng HMAC với engine. Thiếu FIDES_HMAC_KEY thì không khởi động."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine.common.hashing import require_key

NAME = os.environ.get("SERVICE_NAME", "bank-core")


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

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    require_key()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

"""Mission control on this laptop.

    python -m mission_control.server --port 8080 --token devtoken

In the field the coordinator's phone reaches it with `adb reverse tcp:8080
tcp:8080` and http://127.0.0.1:8080/?token=devtoken, so no network setup is
needed beyond the USB cable that is already there. On AWS the same App runs as
a Lambda (lambda_handler.py).
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from .app import App
from .store import MemoryStore


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def _go(self, method: str) -> None:
            u = urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            code, ctype, out = app.handle(method, u.path, dict(self.headers), body, dict(parse_qsl(u.query)))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_POST(self):  # noqa: N802
            self._go("POST")

        def log_message(self, *a):  # quiet
            pass
    return H


def serve(port: int, token: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(App(MemoryStore(), token)))
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", default="devtoken")
    a = ap.parse_args()
    s = serve(a.port, a.token)
    print(f"mission control on http://127.0.0.1:{a.port}/?token={a.token}")
    s.serve_forever()

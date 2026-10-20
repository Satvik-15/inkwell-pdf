"""
Browser dev mode: python -m localpdf.dev_server, then open http://127.0.0.1:8765
Runs the same interface in a normal browser, which is handy for UI work and tests.
Only listens on 127.0.0.1.
"""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import Api

WEB = Path(__file__).resolve().parent / "web"
API = Api()
ALLOWED = {name for name in dir(Api) if not name.startswith("_") and name not in ("open_dialog", "reveal")}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/dev.js":  # marks the page as browser dev mode
            body = b"window.LOCALPDF_DEV = true;"
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        name = self.path.removeprefix("/api/")
        if name not in ALLOWED:
            self.send_error(404)
            return
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
        try:
            result = getattr(API, name)(*args)
            body = json.dumps(result).encode()
            self.send_response(200)
        except Exception as ex:  # report, don't crash the server
            body = json.dumps({"error": str(ex)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(port=8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LocalPDF dev mode: http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)

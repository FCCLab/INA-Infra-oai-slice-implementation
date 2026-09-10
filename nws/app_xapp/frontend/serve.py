#!/usr/bin/env python3
"""Serve the xApp frontend console on a dedicated port (same image as the backend)."""

from __future__ import annotations

import argparse
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
DEFAULT_UI_PORT = int(os.environ.get("NWS_XAPP_UI_PORT", "18081"))
DEFAULT_API_PORT = int(os.environ.get("NWS_XAPP_API_PORT", "18080"))


class ConsoleHandler(SimpleHTTPRequestHandler):
    backend_port = DEFAULT_API_PORT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[xapp-ui] " + (fmt % args), flush=True)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/config.json", "/api/config"):
            host = (self.headers.get("Host") or "127.0.0.1").split(":")[0]
            body = {
                "app": "xapp",
                "backend_port": self.backend_port,
                "api_base": f"http://{host}:{self.backend_port}",
                "docs": f"http://{host}:{self.backend_port}/docs",
                "health": f"http://{host}:{self.backend_port}/health",
            }
            raw = json.dumps(body, indent=2).encode("utf-8") + b"\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path in ("/", "/index.html", "/gui", "/ui", "/console"):
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)


def main() -> int:
    ap = argparse.ArgumentParser(description="xApp frontend console")
    ap.add_argument("--host", default=os.environ.get("NWS_XAPP_UI_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    ap.add_argument("--backend-port", type=int, default=DEFAULT_API_PORT)
    args = ap.parse_args()
    ConsoleHandler.backend_port = args.backend_port
    httpd = ThreadingHTTPServer((args.host, args.port), ConsoleHandler)
    print(
        f"xApp console on http://{args.host}:{args.port}  (backend :{args.backend_port})",
        flush=True,
    )
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

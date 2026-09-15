#!/usr/bin/env python3
"""Serve the rApp frontend console on a dedicated port (same image as the backend)."""

from __future__ import annotations

import argparse
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DEFAULT_UI_PORT = int(os.environ.get("NWS_RAPP_UI_PORT", "18091"))
DEFAULT_API_PORT = int(os.environ.get("NWS_RAPP_API_PORT", "18090"))


class ConsoleHandler(SimpleHTTPRequestHandler):
    backend_port = DEFAULT_API_PORT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[rapp-ui] " + (fmt % args), flush=True)

    def _proxy(self, method: str) -> None:
        url = f"http://127.0.0.1:{self.backend_port}{self.path}"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
        if body is not None:
            headers["Content-Length"] = str(len(body))
        req = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(req, timeout=10.0) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-length"):
                        self.send_header(k, v)
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
        except HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in ("transfer-encoding", "content-length"):
                    self.send_header(k, v)
            data = e.read() if e.fp else b""
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            err = json.dumps({"error": f"proxy error to backend :{self.backend_port}: {e}"}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/") or path.startswith("/docs") or path == "/openapi.json":
            self._proxy("GET")
            return
        if path in ("/config.json", "/api/config"):
            host_header = self.headers.get("Host") or "127.0.0.1"
            host = host_header.split(":")[0]
            req_port = int(host_header.split(":")[1]) if ":" in host_header else 80
            api_port = req_port - 1 if req_port in (31091, 31081, 18091, 18081) else self.backend_port
            body = {
                "app": "rapp",
                "backend_port": api_port,
                "api_base": "",
                "docs": f"http://{host}:{api_port}/docs",
                "health": f"http://{host}:{api_port}/health",
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

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy("PATCH")


def main() -> int:
    ap = argparse.ArgumentParser(description="rApp frontend console")
    ap.add_argument("--host", default=os.environ.get("NWS_RAPP_UI_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    ap.add_argument("--backend-port", type=int, default=DEFAULT_API_PORT)
    args = ap.parse_args()
    ConsoleHandler.backend_port = args.backend_port
    httpd = ThreadingHTTPServer((args.host, args.port), ConsoleHandler)
    print(
        f"rApp console on http://{args.host}:{args.port}  (backend :{args.backend_port})",
        flush=True,
    )
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

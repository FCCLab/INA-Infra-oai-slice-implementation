#!/usr/bin/env python3
"""
End-to-end throughput test for OAI gNB + Open5GS using iperf3 (not YAML validation).

Typical flow (after UE has a PDU session and routes exist):
  1) Start an iperf3 server reachable from the UE (host N6, core container, or remote).
  2) Run iperf3 client from the UE side (host with oaitun route, netns, or UE container).

Examples:
  # Server already running on N6 bridge (e.g. host 10.47.0.1), UL from UE netns:
  sudo python3 e2e_slice_iperf.py run --server 10.47.0.1 --port 5201 \\
      --client-prefix "ip netns exec oai_ue0" --ul --dl --time 15

  # Script starts a local iperf3 server, then runs client on same machine (lab routing):
  python3 e2e_slice_iperf.py run --server 127.0.0.1 --start-local-server --port 5202 \\
      --time 10 --ul

  # iperf3 inside Open5GS container (servers 5201/5000-5003 may already exist from entrypoint):
  python3 e2e_slice_iperf.py run --server 10.47.0.2 --port 5201 --time 10 --ul --reverse

Requires: iperf3 (3.x), ping (optional --no-ping).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional


def _which(name: str) -> Optional[str]:
    from shutil import which

    return which(name)


def _require(cmd: str, name: str) -> str:
    path = _which(cmd)
    if not path:
        print(f"Missing required executable: {name} ({cmd})", file=sys.stderr)
        sys.exit(2)
    return path


def ping_once(host: str, count: int = 3, timeout_s: float = 2.0) -> tuple[bool, str]:
    ping = _which("ping")
    if not ping:
        return False, "ping not installed"
    try:
        r = subprocess.run(
            [ping, "-c", str(count), "-W", str(int(timeout_s)), host],
            capture_output=True,
            text=True,
            timeout=timeout_s * count + 5,
        )
        ok = r.returncode == 0
        tail = (r.stdout or "") + (r.stderr or "")
        return ok, tail.strip()[-500:]
    except subprocess.TimeoutExpired:
        return False, "ping timed out"


def wait_tcp(host: str, port: int, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def parse_client_prefix(prefix: Optional[str]) -> list[str]:
    if not prefix or not prefix.strip():
        return []
    return shlex.split(prefix)


@dataclass
class IperfResult:
    direction: str  # "ul" or "dl"
    megabits_per_second: float
    raw_json: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _bps_to_mbps(bps: float) -> float:
    return bps / 1e6


def parse_iperf3_json(text: str) -> tuple[float, dict[str, Any]]:
    """Return (throughput Mbps in direction of interest, full JSON)."""
    data = json.loads(text)
    # TCP: end.sum_sent / sum_received. UDP with -P: aggregate in end.sum; per-stream in end.streams[].udp
    end = data.get("end") or {}
    sent = end.get("sum_sent") or {}
    recv = end.get("sum_received") or {}
    bps_sent = float(sent.get("bits_per_second") or 0)
    bps_recv = float(recv.get("bits_per_second") or 0)
    bps = max(bps_sent, bps_recv)
    if bps <= 0:
        sm = end.get("sum")
        if isinstance(sm, dict):
            bps = float(sm.get("bits_per_second") or 0)
    if bps <= 0:
        total = 0.0
        for st in end.get("streams") or []:
            if not isinstance(st, dict):
                continue
            for key in ("udp", "tcp"):
                u = st.get(key)
                if isinstance(u, dict):
                    total += float(u.get("bits_per_second") or 0)
                    break
        bps = total
    mbps = _bps_to_mbps(bps)
    return mbps, data


def run_iperf3_client(
    iperf3: str,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    client_prefix: list[str],
    omit_sec: int = 0,
) -> IperfResult:
    direction = "dl" if reverse else "ul"
    cmd = list(client_prefix)
    cmd += [
        iperf3,
        "-c",
        server,
        "-p",
        str(port),
        "-t",
        str(duration),
        "-J",
        "--connect-timeout",
        "5000",
    ]
    if reverse:
        cmd.append("-R")
    if omit_sec > 0:
        cmd.extend(["-O", str(omit_sec)])
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 60,
        )
    except subprocess.TimeoutExpired as e:
        return IperfResult(direction=direction, megabits_per_second=0.0, error=str(e))

    out = r.stdout or ""
    if r.returncode != 0:
        err = (r.stderr or "") + out
        return IperfResult(
            direction=direction,
            megabits_per_second=0.0,
            error=f"iperf3 exit {r.returncode}: {err[-2000:]}",
        )
    try:
        mbps, raw = parse_iperf3_json(out)
    except json.JSONDecodeError as e:
        return IperfResult(
            direction=direction,
            megabits_per_second=0.0,
            error=f"Bad JSON from iperf3: {e}\n{out[-1500:]}",
        )
    return IperfResult(direction=direction, megabits_per_second=mbps, raw_json=raw)


class LocalIperfServer:
    """Background `iperf3 -s` on a port (local host)."""

    def __init__(self, iperf3: str, port: int, bind: str = "0.0.0.0"):
        self.iperf3 = iperf3
        self.port = port
        self.bind = bind
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self._proc and self._poll() is None:
            return
        self._proc = subprocess.Popen(
            [self.iperf3, "-s", "-p", str(self.port), "-B", self.bind],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait until port accepts connections on loopback at least
        ok = wait_tcp("127.0.0.1", self.port, timeout_s=10.0)
        if not ok:
            self.stop()
            raise RuntimeError(f"iperf3 server did not open port {self.port} in time")

    def _poll(self) -> Optional[int]:
        if self._proc is None:
            return -1
        return self._proc.poll()

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            os.killpg(self._proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._proc = None


def cmd_run(args: argparse.Namespace) -> int:
    iperf3 = _require("iperf3", "iperf3")
    prefix = parse_client_prefix(args.client_prefix)

    server_host = args.server
    port = args.port

    local_srv: Optional[LocalIperfServer] = None
    if args.start_local_server:
        local_srv = LocalIperfServer(iperf3, port, bind=args.bind)
        print(f"Starting local iperf3 server on {args.bind}:{port} ...", flush=True)
        local_srv.start()
        print("Server is up.", flush=True)

    results: list[IperfResult] = []
    try:
        if not args.no_ping:
            ok, msg = ping_once(server_host, count=args.ping_count)
            print(f"ping {server_host}: {'OK' if ok else 'FAIL'}", flush=True)
            if not ok:
                print(msg, file=sys.stderr)
                if args.require_ping:
                    return 1
            elif args.verbose:
                print(msg, flush=True)

        if not wait_tcp(server_host, port, timeout_s=args.connect_wait):
            print(
                f"TCP {server_host}:{port} not reachable (iperf3 server running? firewall?).",
                file=sys.stderr,
            )
            return 1

        if args.ul:
            print(f"UL iperf3 -> {server_host}:{port} ({args.time}s) ...", flush=True)
            results.append(
                run_iperf3_client(
                    iperf3,
                    server_host,
                    port,
                    args.time,
                    reverse=False,
                    client_prefix=prefix,
                    omit_sec=args.omit,
                )
            )
        if args.dl:
            print(f"DL iperf3 (-R) -> {server_host}:{port} ({args.time}s) ...", flush=True)
            results.append(
                run_iperf3_client(
                    iperf3,
                    server_host,
                    port,
                    args.time,
                    reverse=True,
                    client_prefix=prefix,
                    omit_sec=args.omit,
                )
            )

        fail = False
        for r in results:
            tag = r.direction.upper()
            if r.error:
                print(f"{tag}: ERROR {r.error}", flush=True)
                fail = True
                continue
            print(f"{tag}: {r.megabits_per_second:.3f} Mbps", flush=True)
            floor = args.min_mbps_ul if r.direction == "ul" else args.min_mbps_dl
            if floor is not None and r.megabits_per_second < floor:
                print(
                    f"{tag}: FAIL below threshold ({r.megabits_per_second:.3f} < {floor})",
                    file=sys.stderr,
                )
                fail = True

        if args.json_out:
            payload = [
                {
                    "direction": r.direction,
                    "mbps": r.megabits_per_second,
                    "error": r.error,
                }
                for r in results
            ]
            print(json.dumps(payload, indent=2))

        return 1 if fail else 0
    finally:
        if local_srv is not None:
            local_srv.stop()
            print("Stopped local iperf3 server.", flush=True)


def cmd_serve(args: argparse.Namespace) -> int:
    iperf3 = _require("iperf3", "iperf3")
    cmd = [iperf3, "-s", "-p", str(args.port), "-B", args.bind]
    if args.daemon:
        cmd.append("-D")
        r = subprocess.run(cmd)
        return r.returncode
    os.execvp(iperf3, cmd)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="E2E iperf3 tests (UE path through gNB/core). "
        "Ensure PDU session and routing are up before running."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    runp = sub.add_parser("run", help="Ping (optional) + iperf3 client toward server")
    runp.add_argument("--server", required=True, help="iperf3 server IP/DNS (destination for -c)")
    runp.add_argument("--port", type=int, default=5201, help="iperf3 TCP port")
    runp.add_argument("--time", "-t", type=int, default=10, dest="time", help="test duration per direction")
    runp.add_argument("--ul", action="store_true", help="run uplink (client sends)")
    runp.add_argument("--dl", action="store_true", help="run downlink (iperf3 -R)")
    runp.add_argument(
        "--client-prefix",
        default="",
        help='Optional argv prefix for client only, e.g. \'sudo ip netns exec ue1\'',
    )
    runp.add_argument(
        "--start-local-server",
        action="store_true",
        help="spawn iperf3 -s on this machine (use with lab routing; not for real UE attach)",
    )
    runp.add_argument(
        "--bind",
        default="0.0.0.0",
        help="with --start-local-server, bind address for iperf3 -s",
    )
    runp.add_argument("--no-ping", action="store_true", help="skip ICMP check")
    runp.add_argument("--require-ping", action="store_true", help="fail if ping fails")
    runp.add_argument("--ping-count", type=int, default=3, dest="ping_count")
    runp.add_argument("--connect-wait", type=float, default=15.0, dest="connect_wait")
    runp.add_argument("--omit", "-O", type=int, default=0, help="omit first N seconds (iperf3 -O)")
    runp.add_argument("--min-mbps-ul", type=float, default=None, dest="min_mbps_ul")
    runp.add_argument("--min-mbps-dl", type=float, default=None, dest="min_mbps_dl")
    runp.add_argument("--json-out", action="store_true", help="print JSON summary to stdout")
    runp.add_argument("-v", "--verbose", action="store_true")
    runp.set_defaults(func=cmd_run)

    srv = sub.add_parser("serve", help="Run iperf3 -s in foreground (or -D)")
    srv.add_argument("--port", "-p", type=int, default=5201)
    srv.add_argument("--bind", "-B", default="0.0.0.0")
    srv.add_argument("-D", "--daemon", action="store_true", help="daemonize (iperf3 -D)")
    srv.set_defaults(func=cmd_serve)

    args = ap.parse_args()

    if args.cmd == "run":
        if not args.ul and not args.dl:
            args.ul = True

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

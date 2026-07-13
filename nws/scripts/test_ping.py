#!/usr/bin/env python3
"""
Ping UPF from all running OAI NR UE containers.

Default: list running UE containers, ping each 5 times (no tmux).
Optional --tmux: one pane per UE, ping forever (auto-retry).

Examples:
  python3 test_ping.py
  python3 test_ping.py --host 10.45.0.1
  python3 test_ping.py --count 10
  python3 test_ping.py --tmux
  python3 test_ping.py --tmux --session nws_ping
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from typing import Optional

DEFAULT_PING_HOST = "10.45.0.1"
UE_NAME_RE = re.compile(r"^nws-oai-nr-ue(\d+)$")
OAITUN_CANDIDATES = ("oaitun_ue0", "oaitun_ue1")


def run(argv: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def list_running_ue_containers() -> list[str]:
    """Return running UE container names sorted by UE index (ue1, ue2, ...)."""
    r = run(
        [
            "docker",
            "ps",
            "--format",
            "{{.Names}}",
            "--filter",
            "status=running",
            "--filter",
            "name=nws-oai-nr-ue",
        ]
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"docker ps failed: {err}")

    found: list[tuple[int, str]] = []
    for line in (r.stdout or "").splitlines():
        name = line.strip()
        m = UE_NAME_RE.match(name)
        if m:
            found.append((int(m.group(1)), name))
    found.sort(key=lambda t: t[0])
    return [name for _, name in found]


def detect_oaitun(container: str) -> Optional[str]:
    r = run(["docker", "exec", container, "ip", "-4", "-o", "addr", "show"])
    if r.returncode != 0:
        return None
    text = r.stdout or ""
    for iface in OAITUN_CANDIDATES:
        if re.search(rf"\b{re.escape(iface)}\b", text):
            return iface
    # Any oaitun_* with an inet address
    m = re.search(r"^\d+:\s+(oaitun_ue\d+)\s+inet\s+", text, re.MULTILINE)
    return m.group(1) if m else None


def ping_once_batch(
    container: str,
    host: str,
    count: int,
    iface: Optional[str],
) -> int:
    """Run ping -c N inside container; return exit code."""
    ifaces = (iface,) if iface else OAITUN_CANDIDATES
    last_rc = 1
    for ifc in ifaces:
        argv = [
            "docker",
            "exec",
            container,
            "ping",
            "-c",
            str(count),
            "-W",
            "2",
            "-I",
            ifc,
            host,
        ]
        print(f"=== {container} -> {host} via {ifc} ({count} packets) ===")
        # Stream output live
        p = subprocess.run(argv)
        last_rc = p.returncode
        if p.returncode == 0:
            return 0
    return last_rc


def forever_ping_cmd(container: str, host: str, iface: str, retry_delay: float) -> list[str]:
    """Argv for a pane: bash loop that pings forever and retries on exit."""
    script = (
        f"echo '=== {container} ping {host} via {iface} (forever) ==='; "
        f"while true; do "
        f"docker exec {shlex.quote(container)} ping {shlex.quote(host)} -I {shlex.quote(iface)}; "
        f"rc=$?; "
        f'echo "=== {container} ping exited rc=$rc; retry in {retry_delay}s ==="; '
        f"sleep {retry_delay}; "
        f"done"
    )
    return ["bash", "-lc", script]


def open_tmux(
    containers: list[str],
    host: str,
    session: str,
    retry_delay: float,
    ifaces: dict[str, str],
) -> int:
    if not shutil.which("tmux"):
        print("tmux not found; install tmux or run without --tmux", file=sys.stderr)
        return 1

    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0:
        print(f"Killing existing tmux session {session}")
        subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    n = len(containers)
    first = containers[0]
    cmd0 = forever_ping_cmd(first, host, ifaces[first], retry_delay)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-n", "ue", *cmd0],
        check=True,
    )

    for cname in containers[1:]:
        cmd = forever_ping_cmd(cname, host, ifaces[cname], retry_delay)
        subprocess.run(
            ["tmux", "split-window", "-t", f"{session}:ue", *cmd],
            check=True,
        )
        subprocess.run(["tmux", "select-layout", "-t", f"{session}:ue", "tiled"], check=False)

    subprocess.run(["tmux", "select-layout", "-t", f"{session}:ue", "tiled"], check=False)
    subprocess.run(["tmux", "set-option", "-t", session, "mouse", "on"], check=False)

    print(f"tmux session: {session}  |  {n} UE pane(s), ping forever -> {host}")
    print("Detach: Ctrl-b then d")
    print(f"Kill:   tmux kill-session -t {session}")
    return subprocess.call(["tmux", "attach", "-t", session])


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ping from running nws-oai-nr-ue* containers (default: 5 pings, no tmux)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--host", default=DEFAULT_PING_HOST, help="Ping target (UPF / N3 gateway)")
    ap.add_argument(
        "--count",
        type=int,
        default=5,
        help="Ping count when not using --tmux",
    )
    ap.add_argument(
        "--tmux",
        action="store_true",
        help="Open tmux with one pane per UE and ping forever",
    )
    ap.add_argument(
        "--session",
        default="nws_ping",
        help="tmux session name (with --tmux)",
    )
    ap.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Seconds between forever-ping retries in tmux",
    )
    ap.add_argument(
        "--iface",
        default=None,
        help="Force oaitun iface (default: auto-detect oaitun_ue0/ue1)",
    )
    ap.add_argument(
        "--list-only",
        action="store_true",
        help="Only list running UE containers",
    )
    args = ap.parse_args()

    try:
        containers = list_running_ue_containers()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    if not containers:
        print("No running UE containers matching nws-oai-nr-ue*")
        return 1

    print(f"Running UE containers ({len(containers)}):")
    for name in containers:
        print(f"  {name}")
    if args.list_only:
        return 0

    ifaces: dict[str, str] = {}
    for name in containers:
        ifc = args.iface or detect_oaitun(name)
        if not ifc:
            print(f"WARN: no oaitun on {name}; will try {OAITUN_CANDIDATES}", file=sys.stderr)
            ifc = OAITUN_CANDIDATES[0]
        ifaces[name] = ifc
        print(f"  {name}: iface={ifc}")

    if args.tmux:
        return open_tmux(
            containers,
            args.host,
            args.session,
            args.retry_delay,
            ifaces,
        )

    failed: list[str] = []
    for name in containers:
        rc = ping_once_batch(name, args.host, args.count, ifaces.get(name))
        if rc != 0:
            failed.append(name)

    if failed:
        print(f"FAIL: ping failed for: {', '.join(failed)}")
        return 1
    print(f"OK: all {len(containers)} UE(s) pinged {args.host} ({args.count} packets each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

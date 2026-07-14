#!/usr/bin/env python3
"""
iperf3 throughput from all running OAI NR UE containers.

Default: list UEs, ensure iperf3 servers in nws-5gc, run sequential UL then DL
(finite duration, no tmux).

Examples:
  python3 test_throughput.py
  python3 test_throughput.py --dir ul
  python3 test_throughput.py --dir dl --mode parallel
  python3 test_throughput.py --dir both --mode sequential --time 20
  python3 test_throughput.py --tmux --dir ul          # one pane/UE, forever
  python3 test_throughput.py --ue1 --dir ul           # only UE1
  python3 test_throughput.py --ue1 --ue3 --tmux       # UE1+UE3
  python3 test_throughput.py -u --bitrate 100M         # UDP
  python3 test_throughput.py -t --tmux --dir UL        # TCP (default)
"""

from __future__ import annotations

import argparse
import atexit
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

CORE_CONTAINER = "nws-5gc"
DEFAULT_SERVER = "10.47.0.2"  # Open5GS N6
DEFAULT_PORT = 5201
DEFAULT_TIME = 20
UE_NAME_RE = re.compile(r"^nws-oai-nr-ue(\d+)$")
OAITUN_CANDIDATES = ("oaitun_ue0", "oaitun_ue1")
# Fallback static PDU IPs (subscriber DB)
STATIC_UE_IP = {
    1: "10.45.0.31",
    2: "10.45.0.32",
    3: "10.45.0.33",
    4: "10.45.0.34",
    5: "10.45.0.35",
}


def run(argv: list[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def docker_exec(
    container: str,
    cmd: list[str],
    *,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    return run(["docker", "exec", container, *cmd], timeout=timeout)


def list_running_ue_containers() -> list[str]:
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
        raise RuntimeError(f"docker ps failed: {(r.stderr or r.stdout or '').strip()}")
    found: list[tuple[int, str]] = []
    for line in (r.stdout or "").splitlines():
        name = line.strip()
        m = UE_NAME_RE.match(name)
        if m:
            found.append((int(m.group(1)), name))
    found.sort(key=lambda t: t[0])
    return [n for _, n in found]


def ue_index(container: str) -> int:
    m = UE_NAME_RE.match(container)
    if not m:
        raise ValueError(container)
    return int(m.group(1))


def detect_oaitun_and_ip(container: str) -> tuple[Optional[str], Optional[str]]:
    r = docker_exec(container, ["ip", "-4", "-o", "addr", "show"], timeout=15)
    if r.returncode != 0:
        return None, None
    text = r.stdout or ""
    for iface in OAITUN_CANDIDATES:
        m = re.search(
            rf"^\d+:\s+{re.escape(iface)}\s+inet\s+([\d.]+)/",
            text,
            re.MULTILINE,
        )
        if m:
            return iface, m.group(1)
    m = re.search(r"^\d+:\s+(oaitun_ue\d+)\s+inet\s+([\d.]+)/", text, re.MULTILINE)
    if m:
        return m.group(1), m.group(2)
    return None, None


_MBITS_RE = re.compile(
    r"^\[[\s\d]+\]\s+[\d.]+\s*-\s*([\d.]+)\s+sec\s+.*?([\d.]+)\s*Mbits/sec"
    r"(?:\s+\d+)?\s+(sender|receiver)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_MBITS_ANY_RE = re.compile(r"([\d.]+)\s*Mbits/sec")

_print_lock = threading.Lock()


def parse_iperf3_text(text: str, *, reverse: bool) -> float:
    """
    Mbps from human-readable iperf3 output.
    Prefer final sender (UL) or receiver (DL / -R) summary row.
    """
    want = "receiver" if reverse else "sender"
    best: Optional[float] = None
    best_end = -1.0
    for m in _MBITS_RE.finditer(text or ""):
        end_s, mbps_s, role = m.group(1), m.group(2), m.group(3).lower()
        if role != want:
            continue
        end = float(end_s)
        mbps = float(mbps_s)
        if end >= best_end:
            best_end = end
            best = mbps
    if best is not None:
        return best
    # Fallback: last Mbits/sec on a SUM or long-interval line
    last = None
    for line in (text or "").splitlines():
        if "Mbits/sec" not in line:
            continue
        if "sender" in line.lower() or "receiver" in line.lower() or "SUM" in line:
            mm = _MBITS_ANY_RE.search(line)
            if mm:
                last = float(mm.group(1))
    if last is not None:
        return last
    raise ValueError("no Mbits/sec summary in iperf3 output")


def stream_docker_exec(
    container: str,
    cmd: list[str],
    *,
    prefix: str,
    timeout: float,
) -> tuple[int, str]:
    """
    Run docker exec and forward stdout/stderr live (line-prefixed).
    Returns (exit_code, full_captured_text).
    """
    argv = ["docker", "exec", container, *cmd]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        return 1, str(e)

    chunks: list[str] = []
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout

    def _emit(line: str) -> None:
        chunks.append(line)
        with _print_lock:
            sys.stdout.write(f"{prefix}{line}")
            if not line.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                _emit(f"[timeout after {timeout:.0f}s]\n")
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return 124, "".join(chunks)
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                _emit(line)
        # Drain remainder
        rest = proc.stdout.read() or ""
        if rest:
            for ln in rest.splitlines(keepends=True):
                _emit(ln)
        return proc.wait(), "".join(chunks)
    except Exception as e:
        proc.kill()
        return 1, "".join(chunks) + f"\n{e}"


def container_running(name: str) -> bool:
    r = run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return r.returncode == 0 and (r.stdout or "").strip().lower() == "true"


def iperf_listening(core: str, port: int) -> bool:
    r = docker_exec(
        core,
        ["bash", "-c", f"ss -tlnp 2>/dev/null | grep -q ':{port}'"],
        timeout=10,
    )
    return r.returncode == 0


def clear_iperf3(
    containers: list[str],
    *,
    ports: Optional[list[int]] = None,
    core: Optional[str] = None,
    quiet: bool = False,
) -> None:
    """Kill iperf3 processes matching the given ports (SIGKILL) and loop until none remain.

    If *ports* is given, only processes using those specific ports are killed
    (safe to call while another direction's iperf3 is still running).
    If *ports* is None, all iperf3 processes in the container are killed.
    """
    target_containers = list(containers)
    if core:
        target_containers.append(core)

    if ports:
        ports_pat = "|".join(str(p) for p in sorted(ports))
        pgrep_cmd  = ["bash", "-c", f"pgrep -f 'iperf3.*-p ({ports_pat})' > /dev/null 2>&1"]
        pkill_cmd  = ["bash", "-c", f"pkill -9 -f 'iperf3.*-p ({ports_pat})' 2>/dev/null; true"]
    else:
        pgrep_cmd  = ["pgrep", "-x", "iperf3"]
        pkill_cmd  = ["pkill", "-9", "-x", "iperf3"]

    for name in target_containers:
        if not container_running(name):
            continue
        # SIGKILL and verify termination (up to 15 × 200 ms = 3 s)
        for _ in range(15):
            r_check = docker_exec(name, pgrep_cmd, timeout=5)
            if r_check.returncode != 0:
                break  # no matching process found
            docker_exec(name, pkill_cmd, timeout=5)
            time.sleep(0.2)

    if not quiet:
        where = ", ".join(containers)
        if core:
            where += f", {core}"
        print(f"cleared iperf3 on: {where}")


def ensure_iperf_servers(
    core: str,
    ports: list[int],
    bind_addr: Optional[str],
) -> bool:
    if not container_running(core):
        print(f"ERROR: core container {core} is not running", file=sys.stderr)
        return False

    ports_pattern = "|".join(str(p) for p in ports)
    pkill_cmd = f"pkill -f 'iperf3.*-p ({ports_pattern})' 2>/dev/null || true"
    docker_exec(core, ["bash", "-c", pkill_cmd], timeout=20)
    time.sleep(0.5)

    for port in sorted(set(ports)):
        cmd = f"iperf3 -s -p {port} -D"
        if bind_addr:
            cmd = f"iperf3 -s -B {shlex.quote(bind_addr)} -p {port} -D"
        r = docker_exec(core, ["bash", "-c", cmd], timeout=15)
        if r.returncode != 0:
            print(f"ERROR: failed to start iperf3 -s :{port}: {(r.stderr or r.stdout or '')[-400:]}", file=sys.stderr)
            return False

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if all(iperf_listening(core, p) for p in ports):
            print(f"iperf3 servers ready on {core}: {', '.join(str(p) for p in ports)}"
                  + (f" (-B {bind_addr})" if bind_addr else ""))
            return True
        time.sleep(0.2)
    missing = [p for p in ports if not iperf_listening(core, p)]
    print(f"ERROR: iperf3 not listening on ports {missing}", file=sys.stderr)
    return False


def build_iperf_client_cmd(
    *,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    bind_ip: Optional[str],
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float = 1.0,
) -> list[str]:
    cmd = [
        "iperf3",
        "-c",
        server,
        "-p",
        str(port),
        "-t",
        str(duration),
        "-i",
        str(interval),
        "--connect-timeout",
        "5000",
    ]
    if bind_ip:
        cmd.extend(["-B", bind_ip])
    if reverse:
        cmd.append("-R")
    if udp:
        cmd.append("-u")
        if bitrate:
            cmd.extend(["-b", bitrate])
    if streams > 1:
        cmd.extend(["-P", str(streams)])
    return cmd


def run_iperf_one(
    container: str,
    *,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    bind_ip: Optional[str],
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float = 1.0,
) -> tuple[str, str, float, Optional[str]]:
    """Stream iperf3 live; returns (container, direction, mbps, error)."""
    direction = "DL" if reverse else "UL"
    cmd = build_iperf_client_cmd(
        server=server,
        port=port,
        duration=duration,
        reverse=reverse,
        bind_ip=bind_ip,
        udp=udp,
        bitrate=bitrate,
        streams=streams,
        interval=interval,
    )
    label = f"{container} {direction} -> {server}:{port}"
    short = container.replace("nws-oai-nr-", "")
    prefix = f"[{short}/{direction}] "
    with _print_lock:
        print(f"=== {label} (t={duration}s{', UDP' if udp else ', TCP'}) ===")
        sys.stdout.flush()
    rc, text = stream_docker_exec(
        container,
        cmd,
        prefix=prefix,
        timeout=float(duration + 90),
    )
    if rc != 0:
        err = text.strip()
        return container, direction, 0.0, (err[-500:] if err else f"exit {rc}")
    try:
        mbps = parse_iperf3_text(text, reverse=reverse)
        with _print_lock:
            print(f"{prefix}-> summary {mbps:.2f} Mbps")
            sys.stdout.flush()
        return container, direction, mbps, None
    except ValueError as e:
        return container, direction, 0.0, str(e)


def run_sequential(
    containers: list[str],
    ports: dict[str, int],
    bind_ips: dict[str, Optional[str]],
    *,
    directions: list[str],
    server: str,
    duration: int,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float,
) -> list[tuple[str, str, float, Optional[str]]]:
    results: list[tuple[str, str, float, Optional[str]]] = []
    for direction in directions:
        reverse = direction == "DL"
        for cname in containers:
            results.append(
                run_iperf_one(
                    cname,
                    server=server,
                    port=ports[cname],
                    duration=duration,
                    reverse=reverse,
                    bind_ip=bind_ips.get(cname),
                    udp=udp,
                    bitrate=bitrate,
                    streams=streams,
                    interval=interval,
                )
            )
    return results


def run_parallel(
    containers: list[str],
    ports: dict[str, int],
    bind_ips: dict[str, Optional[str]],
    *,
    directions: list[str],
    server: str,
    duration: int,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    interval: float,
) -> list[tuple[str, str, float, Optional[str]]]:
    results: list[tuple[str, str, float, Optional[str]]] = []
    for direction in directions:
        reverse = direction == "DL"
        print(f"=== parallel {direction}: {len(containers)} UE(s) ===")
        with ThreadPoolExecutor(max_workers=len(containers)) as pool:
            futs = {
                pool.submit(
                    run_iperf_one,
                    cname,
                    server=server,
                    port=ports[cname],
                    duration=duration,
                    reverse=reverse,
                    bind_ip=bind_ips.get(cname),
                    udp=udp,
                    bitrate=bitrate,
                    streams=streams,
                    interval=interval,
                ): cname
                for cname in containers
            }
            for fut in as_completed(futs):
                results.append(fut.result())
    results.sort(key=lambda r: (0 if r[1] == "UL" else 1, ue_index(r[0])))
    return results


def forever_iperf_cmd(
    container: str,
    *,
    server: str,
    port: int,
    reverse: bool,
    bind_ip: Optional[str],
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    retry_delay: float,
    interval: float,
    session: str,
) -> list[str]:
    direction = "DL" if reverse else "UL"
    client = build_iperf_client_cmd(
        server=server,
        port=port,
        duration=0,  # forever
        reverse=reverse,
        bind_ip=bind_ip,
        udp=udp,
        bitrate=bitrate,
        streams=streams,
        interval=interval,
    )
    docker_cmd = ["docker", "exec", "-t", container, *client]
    docker_q = " ".join(shlex.quote(a) for a in docker_cmd)
    # On pane/session exit, kill the container-side client (docker exec often orphans otherwise).
    cleanup = (
        f"docker exec {shlex.quote(container)} "
        f"bash -c 'pkill -x iperf3 2>/dev/null || true' >/dev/null 2>&1 || true"
    )
    sess = shlex.quote(session)
    # Ctrl-C is delivered to the pane's foreground process. If that is `docker exec -t`,
    # iperf often exits 0 and bash never sees INT — so we background docker and wait,
    # so Ctrl-C hits this shell, then tear down the whole tmux session.
    script = (
        f"cleanup() {{ {cleanup}; }}; "
        f"stop_all() {{ "
        f"trap - EXIT INT TERM; "
        f'[ -n "${{pid:-}}" ] && kill "$pid" 2>/dev/null; '
        f"wait \"$pid\" 2>/dev/null; "
        f"cleanup; "
        f"tmux kill-session -t {sess} 2>/dev/null || true; "
        f"exit 130; "
        f"}}; "
        f"trap stop_all INT TERM; "
        f"trap cleanup EXIT; "
        f"echo '=== {container} {direction} forever -> {server}:{port} "
        f"(report every {interval:g}s; Ctrl-C stops all) ==='; "
        f"while true; do "
        f"{docker_q} & "
        f"pid=$!; "
        f"wait \"$pid\"; "
        f"rc=$?; "
        f"pid=; "
        f'if [ "$rc" -eq 130 ] || [ "$rc" -gt 128 ]; then '
        f'echo "=== {container} {direction} interrupted (rc=$rc); stopping ==="; '
        f"stop_all; "
        f"fi; "
        f'echo "=== {container} {direction} exited rc=$rc; retry in {retry_delay}s ==="; '
        f"sleep {retry_delay}; "
        f"done"
    )
    return ["bash", "-lc", script]


def _tmux_session_alive(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    ).returncode == 0


def open_tmux(
    containers: list[str],
    ports: dict[str, int],
    bind_ips: dict[str, Optional[str]],
    *,
    direction: str,
    server: str,
    session: str,
    udp: bool,
    bitrate: Optional[str],
    streams: int,
    retry_delay: float,
    interval: float,
    core: str,
) -> int:
    if not shutil.which("tmux"):
        print("tmux not found; install tmux or run without --tmux", file=sys.stderr)
        return 1
    if _tmux_session_alive(session):
        print(f"Killing existing tmux session {session}")
        subprocess.run(["tmux", "kill-session", "-t", session], check=False)
        clear_iperf3(containers, ports=list(ports.values()), core=core, quiet=True)

    reverse = direction == "DL"
    first = containers[0]
    cmd0 = forever_iperf_cmd(
        first,
        server=server,
        port=ports[first],
        reverse=reverse,
        bind_ip=bind_ips.get(first),
        udp=udp,
        bitrate=bitrate,
        streams=streams,
        retry_delay=retry_delay,
        interval=interval,
        session=session,
    )
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-n", "ue", *cmd0], check=True)
    for cname in containers[1:]:
        cmd = forever_iperf_cmd(
            cname,
            server=server,
            port=ports[cname],
            reverse=reverse,
            bind_ip=bind_ips.get(cname),
            udp=udp,
            bitrate=bitrate,
            streams=streams,
            retry_delay=retry_delay,
            interval=interval,
            session=session,
        )
        subprocess.run(["tmux", "split-window", "-t", f"{session}:ue", *cmd], check=True)
        subprocess.run(["tmux", "select-layout", "-t", f"{session}:ue", "tiled"], check=False)

    subprocess.run(["tmux", "select-layout", "-t", f"{session}:ue", "tiled"], check=False)
    subprocess.run(["tmux", "set-option", "-t", session, "mouse", "on"], check=False)
    print(
        f"tmux session: {session}  |  {len(containers)} UE pane(s), "
        f"{direction} forever -> {server}  (-i {interval:g}s)"
    )
    print("Ctrl-C in any pane stops all UEs and clears iperf3")
    print(f"Also: tmux kill-session -t {session}")

    def _stop_test(_signum=None, _frame=None) -> None:
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    prev_int = signal.signal(signal.SIGINT, _stop_test)
    prev_term = signal.signal(signal.SIGTERM, _stop_test)
    try:
        rc = subprocess.call(["tmux", "attach", "-t", session])
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        if _tmux_session_alive(session):
            subprocess.run(["tmux", "kill-session", "-t", session], check=False)
        clear_iperf3(containers, ports=list(ports.values()), core=core)
    return rc


def print_summary(results: list[tuple[str, str, float, Optional[str]]]) -> int:
    print("\n=== summary (Mbps) ===")
    print(f"{'UE':<18} {'dir':<4} {'Mbps':>10}  status")
    failed = 0
    for cname, direction, mbps, err in results:
        if err:
            failed += 1
            print(f"{cname:<18} {direction:<4} {'—':>10}  FAIL {err[:80]}")
        else:
            print(f"{cname:<18} {direction:<4} {mbps:10.2f}  OK")
    # Totals per direction
    for direction in ("UL", "DL"):
        vals = [m for c, d, m, e in results if d == direction and not e]
        if vals:
            print(f"  {direction} sum={sum(vals):.2f}  median={sorted(vals)[len(vals)//2]:.2f}  n={len(vals)}")
    return 1 if failed else 0


def parse_directions(value: str) -> list[str]:
    v = value.strip().lower()
    if v in ("ul", "uplink"):
        return ["UL"]
    if v in ("dl", "downlink"):
        return ["DL"]
    if v in ("both", "uldl", "dlul", "all"):
        return ["UL", "DL"]
    raise argparse.ArgumentTypeError("--dir must be ul, dl, or both")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="iperf3 throughput from running nws-oai-nr-ue* (default: sequential UL+DL, no tmux)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--dir",
        type=parse_directions,
        default=parse_directions("UL"),
        help="ul | dl | both (default: ul; use --dir dl in a second terminal for DL)",
    )
    ap.add_argument(
        "--mode",
        choices=("sequential", "parallel"),
        default="sequential",
        help="Run UEs one-by-one or all at once",
    )
    ap.add_argument("--server", default=DEFAULT_SERVER, help="iperf3 server IP (N6 / core)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="Base iperf3 port (UE i uses port+i-1 in parallel)")
    ap.add_argument("--time", type=int, default=DEFAULT_TIME, help="iperf3 -t seconds (ignored with --tmux)")
    ap.add_argument("--streams", type=int, default=1, help="iperf3 -P parallel streams per UE")
    proto = ap.add_mutually_exclusive_group()
    proto.add_argument(
        "-u",
        "--udp",
        action="store_const",
        const="udp",
        dest="proto",
        help="UDP mode (iperf3 -u)",
    )
    proto.add_argument(
        "-t",
        "--tcp",
        action="store_const",
        const="tcp",
        dest="proto",
        help="TCP mode (default)",
    )
    ap.set_defaults(proto="tcp")
    ap.add_argument("--bitrate", default=None, help="UDP -b bitrate (e.g. 100M); ignored for TCP")
    ap.add_argument("--bind-server", default=DEFAULT_SERVER, help="iperf3 -B on core server (empty to disable)")
    ap.add_argument("--no-bind-client", action="store_true", help="Do not pass -B <UE PDU IP> on clients")
    ap.add_argument("--core", default=CORE_CONTAINER, help="Core container hosting iperf3 -s")
    ap.add_argument("--skip-server", action="store_true", help="Do not (re)start iperf3 servers in core")
    ap.add_argument("--tmux", action="store_true", help="One pane per UE, iperf forever (implies parallel)")
    ap.add_argument("--session", default="nws_iperf", help="tmux session name")
    ap.add_argument("--retry-delay", type=float, default=1.0, help="tmux forever retry delay")
    ap.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="iperf3 -i report interval (default: 5 with --tmux, else 1). Longer = smoother lines",
    )
    ap.add_argument("--list-only", action="store_true", help="Only list running UE containers")
    ue_grp = ap.add_argument_group("UE selection (default: all running)")
    for i in range(1, 6):
        ue_grp.add_argument(
            f"--ue{i}",
            action="store_true",
            help=f"Include nws-oai-nr-ue{i}",
        )
    ap.add_argument(
        "--ue",
        type=int,
        action="append",
        dest="ue_nums",
        metavar="N",
        choices=range(1, 6),
        help="Include UE N (repeatable; same as --ueN)",
    )
    args = ap.parse_args()
    args.udp = args.proto == "udp"
    interval = args.interval if args.interval is not None else (5.0 if args.tmux else 1.0)

    selected: set[int] = set()
    for i in range(1, 6):
        if getattr(args, f"ue{i}"):
            selected.add(i)
    if args.ue_nums:
        selected.update(args.ue_nums)

    # argparse may already give a list via type=
    directions: list[str] = args.dir if isinstance(args.dir, list) else parse_directions(str(args.dir))

    try:
        containers = list_running_ue_containers()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    if not containers:
        print("No running UE containers matching nws-oai-nr-ue*")
        return 1

    if selected:
        want = {f"nws-oai-nr-ue{i}" for i in sorted(selected)}
        missing = sorted(want - set(containers))
        containers = [c for c in containers if c in want]
        if missing:
            print(f"NOTE: not running (skipped): {', '.join(missing)}", file=sys.stderr)
        if not containers:
            print(
                f"None of the selected UEs are running: "
                f"{', '.join(f'ue{i}' for i in sorted(selected))}",
                file=sys.stderr,
            )
            return 1

    print(f"Selected UE containers ({len(containers)}):")
    bind_ips: dict[str, Optional[str]] = {}
    dead: list[str] = []
    for name in containers:
        idx = ue_index(name)
        iface, ip = detect_oaitun_and_ip(name)
        if not ip:
            ip = STATIC_UE_IP.get(idx)
        # Skip UEs whose oaitun tunnel is absent (PDU session down).
        if not iface:
            print(f"  {name}: WARNING — no oaitun interface found (PDU session down?); skipping",
                  file=sys.stderr)
            dead.append(name)
            continue
        if args.no_bind_client:
            bind_ips[name] = None
        else:
            bind_ips[name] = ip
        print(f"  {name}: oaitun={iface} pdu={ip or '?'} bind={bind_ips[name] or '(none)'}")

    containers = [c for c in containers if c not in dead]
    if not containers:
        print("ERROR: no UEs with an active oaitun interface; aborting.", file=sys.stderr)
        return 1

    if args.list_only:
        return 0

    # Ports: sequential can share base port; parallel / tmux need one port per UE
    use_multi_port = args.mode == "parallel" or args.tmux
    direction = directions[0] if directions else "UL"
    base_port = args.port
    if args.port == 5201:
        base_port = 5300 if direction == "DL" else 5200

    ports: dict[str, int] = {}
    for name in containers:
        idx = ue_index(name)
        ports[name] = base_port + (idx - 1) if use_multi_port else base_port

    server_ports = sorted({ports[c] for c in containers})
    bind_server = (args.bind_server or "").strip() or None

    # Clear existing clients on our ports in case they are orphaned from a prior run
    clear_iperf3(containers, ports=server_ports, quiet=True)

    if not args.skip_server:
        if not ensure_iperf_servers(args.core, server_ports, bind_server):
            return 1

    session_name = args.session
    if args.session == "nws_iperf":
        session_name = f"{args.session}_{direction}"

    # Finite runs: clear UE clients + core servers on process exit.
    cleaned = {"done": False}

    def _cleanup_atexit() -> None:
        if cleaned["done"]:
            return
        cleaned["done"] = True
        clear_iperf3(containers, ports=list(ports.values()), core=args.core, quiet=False)

    if args.tmux:
        direction = directions[0]
        # open_tmux always clears iperf3 when attach returns / session ends
        return open_tmux(
            containers,
            ports,
            bind_ips,
            direction=direction,
            server=args.server,
            session=session_name,
            udp=args.udp,
            bitrate=args.bitrate,
            streams=args.streams,
            retry_delay=args.retry_delay,
            interval=interval,
            core=args.core,
        )

    atexit.register(_cleanup_atexit)

    print(
        f"Mode={args.mode}  dir={'+'.join(directions)}  server={args.server}  "
        f"time={args.time}s  -i={interval:g}s  proto={'UDP' if args.udp else 'TCP'}"
    )

    try:
        if args.mode == "parallel":
            results = run_parallel(
                containers,
                ports,
                bind_ips,
                directions=directions,
                server=args.server,
                duration=args.time,
                udp=args.udp,
                bitrate=args.bitrate,
                streams=args.streams,
                interval=interval,
            )
        else:
            results = run_sequential(
                containers,
                ports,
                bind_ips,
                directions=directions,
                server=args.server,
                duration=args.time,
                udp=args.udp,
                bitrate=args.bitrate,
                streams=args.streams,
                interval=interval,
            )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    rc = print_summary(results)
    if rc == 0:
        print(f"OK: throughput test finished for {len(containers)} UE(s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())

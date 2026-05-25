#!/usr/bin/env python3
"""
E2E network slicing test orchestrator (Docker): Open5GS core, gNB, 5 NR UEs, iperf3.

Throughput in Mbps does NOT map 1:1 to YAML PRB percentages (RF, MCS, scheduler).
Use tiered checks: L3 ping, per-UE iperf sanity, optional relative share vs min_prb_ratio.

Before testing, the script always runs `docker compose down --remove-orphans` on the RAN stack for a clean start. It does not run `docker compose down` after testing. Every `compose up` uses `--remove-orphans` (core and RAN). By default, Step 3 also rebuilds the gNB binary in the mounted workspace before starting gNB.

Logs: each run creates log_dir/e2e_slice_<timestamp>/ with e2e_slice.log (orchestrator), commands.log (docker/compose/exec plus live [container:...] lines), container_logs_stream/<name>.log (live `docker logs -f` while the orchestrator runs), container_logs/*.log (one-time docker logs snapshot at export), iperf/*.txt (iperf3 -J per UE/mode; default TCP -P 5, -t 20s; use --iperf-udp for UDP -u -b), and throughput_summary.json (max sequential UL/DL Mbps per UE; min_prb_ratio share check uses parallel DL when DL tests are enabled and --dl is set).

Examples:
  python3 e2e_nw_slice_docker.py
  python3 e2e_nw_slice_docker.py --iperf-host 10.47.0.2 --iperf-port 5201 --time 20
  python3 e2e_nw_slice_docker.py --iperf-udp --iperf-parallel 5 --time 20
  python3 e2e_nw_slice_docker.py --stop-after-step4
  python3 e2e_nw_slice_docker.py --stop-after-step5   # then Ctrl+C when done (keeps log streams open)
  python3 e2e_nw_slice_docker.py --skip-start --strict-relative
  python3 e2e_nw_slice_docker.py --with-flexric

Requires: docker, docker compose, python3; iperf3 inside UE/core images; mongosh in nws-5gc.
Optional: PyYAML for --strict-relative slice table (otherwise uses built-in defaults).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import shlex
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
NWS_DIR = SCRIPT_DIR.parent
DEFAULT_CORE_COMPOSE = NWS_DIR / "5gc" / "open5gs" / "docker-compose.yml"
DEFAULT_RAN_COMPOSE = NWS_DIR / "docker-compose.open5gs.5slices.yaml"
DEFAULT_GNB_YAML = NWS_DIR / "gnb.sa.band78.106prb.rfsim.open5gs.5slices.yaml"
DEFAULT_LOG_DIR = NWS_DIR / "logs"

# Docker service/container names (must match docker-compose.open5gs.5slices.yaml)
GNB_CONTAINER_NAME = "nws-oai-gnb"

# Expected subscribers (subscriber_db.csv + nrue*.uicc.yaml); ue_ipv4 = static PDU address
EXPECTED_UES: list[dict[str, Any]] = [
    {"ue": 1, "container": "nws-oai-nr-ue1", "imsi": "001010000000001", "sst": 1, "sd": 0x000001, "ue_ipv4": "10.45.0.31"},
    {"ue": 2, "container": "nws-oai-nr-ue2", "imsi": "001010000000002", "sst": 1, "sd": 0x000002, "ue_ipv4": "10.45.0.32"},
    {"ue": 3, "container": "nws-oai-nr-ue3", "imsi": "001010000000003", "sst": 1, "sd": 0x000003, "ue_ipv4": "10.45.0.33"},
    {"ue": 4, "container": "nws-oai-nr-ue4", "imsi": "001010000000004", "sst": 1, "sd": 0x000004, "ue_ipv4": "10.45.0.34"},
    {"ue": 5, "container": "nws-oai-nr-ue5", "imsi": "001010000000005", "sst": 1, "sd": 0x000005, "ue_ipv4": "10.45.0.35"},
]

# Default min_prb_ratio per UE index 1..5 matching gnb ...5slices.yaml slice_id 1..5
DEFAULT_MIN_PRB_BY_UE = {1: 10.0, 2: 40.0, 3: 10.0, 4: 10.0, 5: 10.0}
# max_prb_ratio (% cap) per UE 1..5 — same file Slices sd 0x000001..0x000005
DEFAULT_MAX_PRB_BY_UE = {1: 100.0, 2: 100.0, 3: 30.0, 4: 30.0, 5: 30.0}
# dedicated_prb_ratio (%) per UE 1..5 — gNB Slices shared row
DEFAULT_DEDICATED_PRB_BY_UE = {1: 10.0, 2: 10.0, 3: 10.0, 4: 10.0, 5: 10.0}


# -----------------------------------------------------------------------------
# iperf JSON (same semantics as e2e_slice_iperf.py)
# -----------------------------------------------------------------------------

def _bps_to_mbps(bps: float) -> float:
    return bps / 1e6


def parse_iperf3_json(text: str) -> tuple[float, dict[str, Any]]:
    """Mbps from iperf3 -J. TCP uses sum_sent/sum_received; UDP -P uses end.sum (or per-stream)."""
    data = json.loads(text)
    end = data.get("end") or {}
    sent = end.get("sum_sent") or {}
    recv = end.get("sum_received") or {}
    bps_sent = float(sent.get("bits_per_second") or 0)
    bps_recv = float(recv.get("bits_per_second") or 0)
    bps = max(bps_sent, bps_recv)
    # UDP with -P (multi-stream): aggregate is in end.sum, not sum_sent/sum_received
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


# -----------------------------------------------------------------------------
# Docker helpers
# -----------------------------------------------------------------------------

# Subprocess transcript (docker / compose / exec); separate from Python logging (e2e_slice.log).
_COMMAND_LOG_PATH: Optional[Path] = None
_command_log_lock = threading.Lock()
_COMMAND_LOG_MAX_CHUNK = 200_000


def set_command_log_file(path: Optional[Path]) -> None:
    """Set path for commands.log (or None to disable). Call once per run after run_dir exists."""
    global _COMMAND_LOG_PATH
    _COMMAND_LOG_PATH = path


def _format_cmdline(argv: list[str]) -> str:
    try:
        return shlex.join([str(x) for x in argv])
    except Exception:
        return " ".join(str(x) for x in argv)


def _snip_stream(s: Optional[str], max_len: int) -> str:
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    half = max_len // 2
    return (
        s[:half]
        + f"\n\n... [{len(s)} characters truncated for commands.log] ...\n\n"
        + s[-half:]
    )


def _append_command_transcript(
    argv: list[str],
    r: subprocess.CompletedProcess,
    *,
    cwd: Optional[Path] = None,
) -> None:
    if _COMMAND_LOG_PATH is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = _snip_stream(r.stdout, _COMMAND_LOG_MAX_CHUNK)
    err = _snip_stream(r.stderr, _COMMAND_LOG_MAX_CHUNK)
    block = (
        f"{'=' * 72}\n"
        f"{ts}  exit={r.returncode}\n"
        f"cwd: {cwd if cwd else '(none)'}\n"
        f"cmd: {_format_cmdline(argv)}\n"
        f"--- stdout ({len(r.stdout or '')} chars) ---\n{out}\n"
        f"--- stderr ({len(r.stderr or '')} chars) ---\n{err}\n\n"
    )
    with _command_log_lock:
        with open(_COMMAND_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(block)


def append_full_text_to_command_log(title: str, text: str) -> None:
    """
    Append raw (untruncated) text to commands.log.

    Useful for very verbose long-running commands (for example gNB rebuild)
    where the generic transcript may be truncated for readability.
    """
    if _COMMAND_LOG_PATH is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = text or ""
    block = (
        f"{'=' * 72}\n"
        f"{ts}  raw_log={title}\n"
        f"--- begin raw output ---\n"
        f"{body}\n"
        f"--- end raw output ---\n\n"
    )
    with _command_log_lock:
        with open(_COMMAND_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(block)


def run_cmd(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
    except subprocess.TimeoutExpired as e:
        # Do not crash long orchestration runs (e.g. hung iperf3); treat as failed command.
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        msg = f"subprocess.TimeoutExpired after {timeout}s: {shlex.join(argv)}\n"
        r = subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout=out,
            stderr=msg + err,
        )
    _append_command_transcript(argv, r, cwd=cwd)
    return r


def docker_available() -> bool:
    r = run_cmd(["docker", "info"])
    return r.returncode == 0


def container_running(name: str) -> bool:
    r = run_cmd(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}",
            name,
        ]
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def container_running_retry(
    name: str,
    *,
    attempts: int = 4,
    delay_s: float = 0.12,
) -> bool:
    """Like container_running, but retry a few times (Docker inspect can flake under concurrent load)."""
    for _ in range(attempts):
        if container_running(name):
            return True
        time.sleep(delay_s)
    return False


def container_health(name: str) -> Optional[str]:
    r = run_cmd(["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", name])
    if r.returncode != 0:
        return None
    s = r.stdout.strip()
    return s if s else None


def docker_exec(
    container: str,
    inner_argv: list[str],
    *,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    return run_cmd(["docker", "exec", container] + inner_argv, timeout=timeout)


def export_container_logs(
    run_dir: Path,
    containers: list[str],
    log: logging.Logger,
    *,
    label: str = "post-run",
) -> None:
    """
    Save `docker logs` for the selected containers into run_dir/container_logs/ (exit snapshot).
    For live logs while the orchestrator runs, see run_dir/container_logs_stream/ from docker logs -f.
    """
    out_dir = run_dir / "container_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    unique = list(dict.fromkeys(c for c in containers if c))
    ts = datetime.now().isoformat(timespec="seconds")
    for cname in unique:
        r = run_cmd(["docker", "logs", "--timestamps", cname], timeout=120)
        path = out_dir / f"{cname}.log"
        hdr = [
            f"# container={cname}",
            f"# exported_at={ts}",
            f"# label={label}",
            f"# exit_code={r.returncode}",
            "#",
            "# This file is a ONE-TIME snapshot from `docker logs` when the orchestrator exported it.",
            "# It does not grow or stream: containers often keep running after the script exits",
            "# (e.g. --stop-after-step5), but nothing appends to this path afterward.",
            f"# Live tail (host): docker logs -f --timestamps {cname}",
            "#",
            "",
        ]
        body = (r.stdout or "")
        err = (r.stderr or "").strip()
        if err:
            body += f"\n# --- docker logs stderr ---\n{err}\n"
        path.write_text("\n".join(hdr) + body, encoding="utf-8")
        if r.returncode != 0:
            log.warning("container log export for %s returned %d", cname, r.returncode)
    log.info(
        "Exported docker logs for %d containers to %s (static snapshot only; "
        "for live output: docker logs -f --timestamps <container>)",
        len(unique),
        out_dir,
    )


def compose_up(
    compose_file: Path,
    services: list[str],
    *,
    cwd: Path,
    build: bool = False,
) -> tuple[bool, str]:
    """Run `docker compose up -d --remove-orphans` for the given services; optional --build."""
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "up",
        "-d",
        "--remove-orphans",
    ]
    if build:
        argv.append("--build")
    argv.extend(services)
    r = run_cmd(argv, cwd=cwd)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out


def compose_down(compose_file: Path, *, cwd: Path) -> tuple[bool, str]:
    """`docker compose down --remove-orphans` to clear stale networks/containers."""
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "down",
        "--remove-orphans",
    ]
    r = run_cmd(argv, cwd=cwd, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out


def compose_run(
    compose_file: Path,
    service: str,
    command: list[str],
    *,
    cwd: Path,
    no_deps: bool = False,
) -> tuple[bool, str]:
    """Run `docker compose run --rm` for one service with explicit command."""
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "run",
        "--rm",
    ]
    if no_deps:
        argv.append("--no-deps")
    argv.append(service)
    argv.extend(command)
    r = run_cmd(argv, cwd=cwd)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out


def _append_stream_start_to_command_log(argv: list[str], *, cwd: Optional[Path]) -> None:
    if _COMMAND_LOG_PATH is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = (
        f"{'=' * 72}\n"
        f"{ts}  streaming=1\n"
        f"cwd: {cwd if cwd else '(none)'}\n"
        f"cmd: {_format_cmdline(argv)}\n"
        f"--- live combined output (stdout+stderr) ---\n"
    )
    with _command_log_lock:
        with open(_COMMAND_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(block)


def _append_stream_line_to_command_log(line: str) -> None:
    if _COMMAND_LOG_PATH is None:
        return
    with _command_log_lock:
        with open(_COMMAND_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)


def _append_stream_end_to_command_log(exit_code: int) -> None:
    if _COMMAND_LOG_PATH is None:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = f"\n--- end live output ---\n{ts}  exit={exit_code}\n\n"
    with _command_log_lock:
        with open(_COMMAND_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(block)


def compose_run_streaming(
    compose_file: Path,
    service: str,
    command: list[str],
    *,
    cwd: Path,
    log: logging.Logger,
    no_deps: bool = False,
) -> tuple[bool, str]:
    """
    Run `docker compose run --rm` and forward combined stdout/stderr in real time.

    Output is streamed only into commands.log (no per-line terminal echo).
    """
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "run",
        "--rm",
    ]
    if no_deps:
        argv.append("--no-deps")
    argv.append(service)
    argv.extend(command)

    _append_stream_start_to_command_log(argv, cwd=cwd)
    p = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    out_parts: list[str] = []
    try:
        if p.stdout is not None:
            for line in p.stdout:
                out_parts.append(line)
                _append_stream_line_to_command_log(line)
    finally:
        rc = p.wait()
        _append_stream_end_to_command_log(rc)

    out = "".join(out_parts)
    return rc == 0, out


def rebuild_gnb_binary(compose_file: Path, *, cwd: Path, log: logging.Logger) -> bool:
    """
    Rebuild gNB + UE binaries inside compose service using mounted workspace sources.

    This rebuilds:
      - `/workspace/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem`
      - `/workspace/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem`
    which are what run_gnb.sh / run_ue.sh execute. It is independent from Docker image layer caching.
    """
    gnb = GNB_CONTAINER_NAME
    build_cmd = (
        "cd /workspace/openairinterface5g/cmake_targets && "
        "./build_oai --ninja --gNB --nrUE --build-e2 --build-lib telnetsrv"
    )
    log.info("Rebuilding gNB + UE binaries in mounted workspace via docker compose run: %s", gnb)
    ok, out = compose_run_streaming(
        compose_file,
        gnb,
        ["bash", "-lc", build_cmd],
        cwd=cwd,
        log=log,
        no_deps=True,
    )
    if not ok:
        log.error("gNB/UE binary rebuild failed: %s", out[-4000:])
        return False
    log.info("gNB + UE binary rebuild completed.")
    return True


def log_ran_compose_failure(
    log: logging.Logger,
    cwd: Path,
    compose_file: Path,
    out: str,
) -> None:
    o = out.lower()
    if "network" in o and "not found" in o:
        log.error(
            "Docker reported a missing network ID (stale endpoint). Clean up manually:\n"
            "  cd %s && docker compose -f %s down --remove-orphans\n"
            "(Normally the script runs this before RAN up; pass --no-ran-compose-down-first only if you skipped it.)",
            cwd,
            compose_file.name,
        )


def wait_container_healthy(
    name: str,
    timeout_s: float,
    log: logging.Logger,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not container_running(name):
            time.sleep(1.0)
            continue
        h = container_health(name)
        if h == "healthy" or h == "none":
            return True
        if h == "unhealthy":
            log.warning("%s reports unhealthy", name)
        time.sleep(1.0)
    return container_running(name) and container_health(name) in ("healthy", "none", None)


def run_container_health_loop(
    stop_event: threading.Event,
    names: list[str],
    interval_s: float,
    log: logging.Logger,
) -> None:
    """Background thread: periodically log Docker running/health for core + RAN containers."""
    while True:
        ok_n = 0
        issues: list[str] = []
        for n in names:
            if not container_running_retry(n):
                issues.append(f"{n}=down")
                continue
            h = container_health(n)
            # OAI images often set HEALTHCHECK that disagrees with a running softmodem; do not WARN on unhealthy alone.
            if h == "unhealthy":
                log.debug("[health] %s State.Health=unhealthy (container still running)", n)
            ok_n += 1
        if issues:
            log.warning("[health] %d/%d up; %s", ok_n, len(names), "; ".join(issues))
        else:
            log.info("[health] %d/%d containers up", ok_n, len(names))
        if stop_event.wait(timeout=interval_s):
            break


def _idle_until_signal_for_log_stream(log: logging.Logger, run_dir: Path) -> None:
    """
    After --stop-after-step5: block so the main process (and docker logs -f children) stay alive.
    Exit with SIGINT (Ctrl+C) or SIGTERM; orchestrator finally then exports container_logs/ snapshots.
    """
    stop_idle = threading.Event()

    def _handler(_signum: int, _frame: Any) -> None:
        stop_idle.set()

    old_int = signal.signal(signal.SIGINT, _handler)
    sigterm_num = getattr(signal, "SIGTERM", None)
    old_term: Any = None
    if sigterm_num is not None:
        try:
            old_term = signal.signal(sigterm_num, _handler)
        except (ValueError, OSError):
            old_term = None

    stream_dir = run_dir / "container_logs_stream"
    log.info(
        "stop-after-step5: idling so log streams keep writing to %s (and [container:…] in commands.log). "
        "Press Ctrl+C (SIGINT) or send SIGTERM to exit; then container_logs/ snapshots are exported.",
        stream_dir,
    )
    minute = 0
    try:
        while not stop_idle.is_set():
            if stop_idle.wait(timeout=60.0):
                break
            minute += 1
            log.info(
                "stop-after-step5: still streaming logs → %s (idle minute %d; Ctrl+C or SIGTERM to exit)",
                stream_dir,
                minute,
            )
        log.info("stop-after-step5: exit signal received; leaving idle loop")
    finally:
        try:
            signal.signal(signal.SIGINT, old_int)
        except ValueError:
            pass
        if old_term is not None and sigterm_num is not None:
            try:
                signal.signal(sigterm_num, old_term)
            except (ValueError, OSError):
                pass


def _wait_container_running_for_logs(
    name: str,
    stop_event: threading.Event,
    *,
    timeout_s: float,
    log: logging.Logger,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return False
        if container_running_retry(name, attempts=2, delay_s=0.05):
            return True
        time.sleep(0.25)
    log.warning("container log stream: %s did not become running within %.0fs", name, timeout_s)
    return False


def run_container_log_follow_streams(
    stop_event: threading.Event,
    names: list[str],
    run_dir: Path,
    log: logging.Logger,
) -> None:
    """
    One `docker logs -f --timestamps` per container: append to run_dir/container_logs_stream/<name>.log
    and mirror each line into commands.log as [container:<name>] ... until stop_event is set.
    """
    out_dir = run_dir / "container_logs_stream"
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Live container log files: %s (docker logs -f per container)", out_dir)

    procs_lock = threading.Lock()
    follow_procs: list[subprocess.Popen[str]] = []

    def container_follow_worker(name: str) -> None:
        if not _wait_container_running_for_logs(name, stop_event, timeout_s=120.0, log=log):
            return
        path = out_dir / f"{name}.log"
        ts = datetime.now().isoformat(timespec="seconds")
        hdr = (
            f"# container={name}\n"
            f"# stream=docker_logs_-f_--timestamps\n"
            f"# started={ts}\n"
            f"# Stops when the orchestrator exits (see commands.log).\n\n"
        )
        proc: Optional[subprocess.Popen[str]] = None
        fh = None
        try:
            fh = open(path, "a", encoding="utf-8")
        except OSError as ex:
            log.warning("container log stream: cannot open %s: %s", path, ex)
            return
        try:
            fh.write(hdr)
            fh.flush()
            proc = subprocess.Popen(
                ["docker", "logs", "-f", "--timestamps", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as ex:
            log.warning("container log stream: failed to start docker logs -f for %s: %s", name, ex)
            try:
                fh.close()
            except Exception:
                pass
            return

        with procs_lock:
            follow_procs.append(proc)

        stdout = proc.stdout
        try:
            if stdout is not None:
                while True:
                    line = stdout.readline()
                    if line == "":
                        break
                    fh.write(line)
                    fh.flush()
                    _append_stream_line_to_command_log(f"[container:{name}] {line}")
        finally:
            try:
                fh.close()
            except Exception:
                pass
            try:
                if proc is not None and proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass

    workers: list[threading.Thread] = []
    for n in names:
        _append_stream_line_to_command_log(f"# [container-stream] follow_start {n}\n")
        t = threading.Thread(
            target=container_follow_worker,
            args=(n,),
            name=f"docker-logs-{n}",
            daemon=True,
        )
        workers.append(t)
        t.start()

    stop_event.wait()

    with procs_lock:
        to_stop = list(follow_procs)
    for p in to_stop:
        try:
            p.terminate()
        except ProcessLookupError:
            pass
    for p in to_stop:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    for t in workers:
        t.join(timeout=8.0)

    for n in names:
        _append_stream_line_to_command_log(f"# [container-stream] follow_stop {n}\n")
    log.info("Container log streaming (docker logs -f) stopped.")


def wait_process_in_container(
    container: str,
    pattern: str,
    timeout_s: float,
    log: logging.Logger,
) -> bool:
    deadline = time.monotonic() + timeout_s
    seen_running = False
    down_streak = 0
    while time.monotonic() < deadline:
        running = container_running_retry(container, attempts=2, delay_s=0.1)
        if not running:
            if seen_running:
                down_streak += 1
                if down_streak >= 3:
                    log.error(
                        "Container %s stopped while waiting for process '%s'.",
                        container,
                        pattern,
                    )
                    return False
            time.sleep(1.0)
            continue

        seen_running = True
        down_streak = 0
        r = docker_exec(container, ["bash", "-c", f"pgrep -f '{pattern}' >/dev/null"])
        if r.returncode == 0:
            return True
        time.sleep(1.0)
    if not container_running(container):
        log.error("Container %s is not running.", container)
    return False


# -----------------------------------------------------------------------------
# Core: ensure + MongoDB subscribers
# -----------------------------------------------------------------------------

def ensure_core(
    compose_file: Path,
    service: str,
    timeout_s: float,
    log: logging.Logger,
    skip_start: bool,
) -> bool:
    cwd = compose_file.parent
    if container_running(service) and container_health(service) in ("healthy", "none", None):
        log.info("Core container %s already running.", service)
        return True
    if skip_start:
        log.error("Core %s not running and --skip-start set.", service)
        return False
    log.info("Starting core via docker compose: %s", compose_file)
    ok, out = compose_up(compose_file, [service], cwd=cwd)
    if not ok:
        log.error("compose up failed: %s", out[-2000:])
        return False
    if not wait_container_healthy(service, timeout_s, log):
        log.error("Core %s did not become healthy in time.", service)
        return False
    log.info("Core %s is up (healthy or no healthcheck).", service)
    return True


def _normalize_sd(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, int):
        return val & 0xFFFFFF
    if isinstance(val, str):
        s = val.strip().lower()
        if s.startswith("0x"):
            return int(s, 16) & 0xFFFFFF
        try:
            return int(s, 16) & 0xFFFFFF if all(c in "0123456789abcdef" for c in s) else int(s) & 0xFFFFFF
        except ValueError:
            return None
    return None


def mongosh_one_subscriber(container: str, imsi: str) -> Optional[dict[str, Any]]:
    # Open5GS WebUI / add_users uses subscribers collection
    js = (
        f'const d=db.subscribers.findOne({{imsi:"{imsi}"}},{{imsi:1,slice:1}});'
        f'print(JSON.stringify(d?d:{{"error":"not_found","imsi":"{imsi}"}}));'
    )
    r = docker_exec(
        container,
        ["mongosh", "open5gs", "--quiet", "--eval", js],
        timeout=30,
    )
    if r.returncode != 0:
        return None
    text = (r.stdout or "").strip()
    if not text:
        return None
    # mongosh may print multiple lines; take last JSON object line
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def verify_subscribers(
    core_container: str,
    log: logging.Logger,
) -> tuple[bool, list[str]]:
    lines: list[str] = []
    all_ok = True
    for exp in EXPECTED_UES:
        imsi = exp["imsi"]
        doc = mongosh_one_subscriber(core_container, imsi)
        if not doc or doc.get("error") == "not_found":
            lines.append(f"FAIL {imsi}: subscriber not found in MongoDB")
            all_ok = False
            log.error("Subscriber missing: %s", imsi)
            continue
        slices = doc.get("slice") or []
        if not slices:
            lines.append(f"FAIL {imsi}: no slice[] in document")
            all_ok = False
            log.error("No slice array for %s", imsi)
            continue
        s0 = slices[0]
        sst = s0.get("sst")
        sd_raw = s0.get("sd")
        sd = _normalize_sd(sd_raw)
        exp_sst = exp["sst"]
        exp_sd = exp["sd"]
        if int(sst) != int(exp_sst) or sd != exp_sd:
            lines.append(
                f"FAIL {imsi}: expected SST={exp_sst} SD=0x{exp_sd:06x}, got SST={sst} SD={sd_raw!r}"
            )
            all_ok = False
            log.error("Slice mismatch for %s", imsi)
        else:
            lines.append(f"PASS {imsi}: SST={sst} SD=0x{sd:06x} (slice OK)")
            log.info("Subscriber OK: %s SST=%s SD=0x%06x", imsi, sst, sd)
    return all_ok, lines


# -----------------------------------------------------------------------------
# RAN: gNB + UEs
# -----------------------------------------------------------------------------

def ensure_ran(
    compose_file: Path,
    timeout_s: float,
    log: logging.Logger,
    skip_start: bool,
    *,
    ran_down_first: bool = True,
    rebuild_gnb: bool = True,
) -> bool:
    """Bring up gNB then 5 UEs in two compose phases to avoid Docker network races."""
    cwd = compose_file.parent
    gnb = GNB_CONTAINER_NAME
    ue_names = [exp["container"] for exp in EXPECTED_UES]
    ran_hint = (
        f"cd {cwd} && docker compose -f {compose_file.name} up -d "
        f"{gnb} {' '.join(ue_names)}"
    )
    if skip_start:
        need = [gnb, *ue_names]
        missing = [n for n in need if not container_running(n)]
        if missing:
            log.error(
                "RAN not ready with --skip-start. Missing or stopped: %s",
                ", ".join(missing),
            )
            log.error(
                "Either remove --skip-start to start gNB + 5 UE containers, or run: %s",
                ran_hint,
            )
            return False
        log.info(
            "Skipping RAN compose (--skip-start); %s + 5 UE containers are running.",
            gnb,
        )
        return True

    if ran_down_first:
        log.info("Stopping prior RAN stack (docker compose down --remove-orphans; default before up)")
        down_ok, down_out = compose_down(compose_file, cwd=cwd)
        if not down_ok:
            log.warning("compose down returned non-zero: %s", down_out[-1000:])
        time.sleep(2)

    if rebuild_gnb:
        if not rebuild_gnb_binary(compose_file, cwd=cwd, log=log):
            return False

    # Phase 1: gNB only (always --build so the gNB image matches the workspace Dockerfile/context)
    log.info("RAN phase 1: starting %s (docker compose up --build)", gnb)
    ok, out = compose_up(compose_file, [gnb], cwd=cwd, build=True)
    if not ok:
        log.error("RAN compose up (gNB) failed: %s", out[-3000:])
        log_ran_compose_failure(log, cwd, compose_file, out)
        if "pull access denied" in out.lower() or "no such image" in out.lower():
            log.error("Build images first: cd %s && docker compose -f %s build", cwd, compose_file.name)
        return False
    if not wait_process_in_container(gnb, "nr-softmodem", min(timeout_s, 120.0), log):
        log.error("nr-softmodem not found in %s after timeout.", gnb)
        return False

    # Phase 2: all UEs (after gNB network endpoints are stable)
    log.info("RAN phase 2: starting %s", ", ".join(ue_names))
    ok, out = compose_up(compose_file, ue_names, cwd=cwd)
    if not ok:
        log.error("RAN compose up (UEs) failed: %s", out[-3000:])
        log_ran_compose_failure(log, cwd, compose_file, out)
        if "pull access denied" in out.lower() or "no such image" in out.lower():
            log.error("Build images first: cd %s && docker compose -f %s build", cwd, compose_file.name)
        return False

    for cname in ue_names:
        if not wait_process_in_container(cname, "nr-uesoftmodem", min(timeout_s, 120.0), log):
            log.error("nr-uesoftmodem not found in %s after timeout.", cname)
            return False
    log.info("RAN containers report softmodem processes.")
    return True


# -----------------------------------------------------------------------------
# Connectivity + iperf
# -----------------------------------------------------------------------------

# `ip -4 -o addr show` lines like: 2: oaitun_ue0    inet 10.45.0.31/24 scope global oaitun_ue0
_OAITUN_INET_LINE = re.compile(r"^\d+:\s+(oaitun_ue\d+)\s+inet\s+([\d.]+)/")


def parse_oaitun_ipv4_map(ip_addr_output: str) -> dict[str, str]:
    """Map oaitun interface name -> IPv4 from `ip -4 -o addr show` output."""
    m: dict[str, str] = {}
    for line in (ip_addr_output or "").splitlines():
        mm = _OAITUN_INET_LINE.match(line.strip())
        if mm:
            m[mm.group(1)] = mm.group(2)
    return m


def get_oaitun_ipv4_map_or_error(ue_container: str) -> tuple[dict[str, str], Optional[str]]:
    """Return oaitun map; if docker exec fails, second element is a short error string for debugging."""
    r = docker_exec(ue_container, ["ip", "-4", "-o", "addr", "show"], timeout=15)
    if r.returncode != 0:
        err = ((r.stderr or "") + (r.stdout or "")).strip()
        return {}, (err[:300] if err else "docker exec failed (non-zero exit)")
    return parse_oaitun_ipv4_map(r.stdout or ""), None


def get_oaitun_ipv4_map(ue_container: str) -> dict[str, str]:
    m, _ = get_oaitun_ipv4_map_or_error(ue_container)
    return m


def wait_for_ue_pdu_tun_ip(
    ue_container: str,
    expected_ipv4: str,
    log: logging.Logger,
    attempts: int,
    interval_s: float,
) -> bool:
    """Poll until expected IPv4 appears on an oaitun_* interface (PDU session / attach)."""
    for i in range(attempts):
        tun_map, exec_err = get_oaitun_ipv4_map_or_error(ue_container)
        if exec_err and (i == 0 or (i + 1) % 6 == 0):
            log.warning(
                "PDU poll: docker exec %s failed (no tun data): %s",
                ue_container,
                exec_err,
            )
        if expected_ipv4 in tun_map.values():
            iface = next(k for k, v in tun_map.items() if v == expected_ipv4)
            log.info("PDU tun OK: %s has %s on %s", ue_container, expected_ipv4, iface)
            return True
        have = list(tun_map.values()) if tun_map else []
        if i == 0 or (i + 1) % 6 == 0 or (i + 1) == attempts:
            log.info(
                "Waiting for PDU tun IP %s on %s (attempt %d/%d, have %s)...",
                expected_ipv4,
                ue_container,
                i + 1,
                attempts,
                have if have else "none",
            )
        else:
            log.debug(
                "PDU tun poll %s attempt %d/%d map=%s",
                ue_container,
                i + 1,
                attempts,
                tun_map,
            )
        if i + 1 < attempts:
            time.sleep(interval_s)
    log.error(
        "Timeout: expected tun IP %s not seen on %s after %d attempts (~%.0fs).",
        expected_ipv4,
        ue_container,
        attempts,
        attempts * interval_s,
    )
    return False


def wait_all_ues_pdu_tun(
    ues: list[dict[str, Any]],
    log: logging.Logger,
    attempts: int,
    interval_s: float,
) -> bool:
    """Wait for each UE container to show its expected static PDU address on oaitun."""
    for exp in ues:
        ip = exp.get("ue_ipv4")
        if not ip:
            log.warning("No ue_ipv4 for %s; skip tun wait.", exp.get("container"))
            continue
        if not wait_for_ue_pdu_tun_ip(exp["container"], str(ip), log, attempts, interval_s):
            return False
    return True


def ping_from_ue_via_oaitun(
    ue_container: str,
    host: str,
    log: logging.Logger,
    oaitun_candidates: tuple[str, ...] = ("oaitun_ue0", "oaitun_ue1"),
) -> bool:
    """
    Ping through the UE PDU tunnel (not Docker eth0). Matches nws/ping_5gc.sh pattern.
    """
    for iface in oaitun_candidates:
        r = docker_exec(
            ue_container,
            ["ping", "-c", "2", "-W", "2", "-I", iface, host],
            timeout=20,
        )
        if r.returncode == 0:
            log.info("L3 ping OK: %s -> %s via %s", ue_container, host, iface)
            return True
        log.debug("ping -I %s %s failed: %s", iface, host, (r.stderr or r.stdout or "")[-400:])
    log.warning(
        "ping via oaitun to %s from %s failed (tried %s)",
        host,
        ue_container,
        oaitun_candidates,
    )
    return False


def wait_pdu_ping(
    ue_container: str,
    host: str,
    log: logging.Logger,
    attempts: int = 36,
    interval_s: float = 5.0,
) -> bool:
    """Retry ping until PDU/tun is up (registration can take tens of seconds)."""
    for i in range(attempts):
        if ping_from_ue_via_oaitun(ue_container, host, log):
            return True
        if i + 1 < attempts:
            log.info(
                "Waiting for UE data path (attempt %d/%d, %.0fs)...",
                i + 1,
                attempts,
                interval_s,
            )
            time.sleep(interval_s)
    return False


def iperf3_in_container(
    ue_container: str,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    log: logging.Logger,
    bind_ip: Optional[str] = None,
    artifact_path: Optional[Path] = None,
    *,
    udp: bool = False,
    parallel: int = 1,
    bitrate: Optional[str] = None,
    iperf_server_cmd: Optional[str] = None,
) -> tuple[float, Optional[str]]:
    """If bind_ip is set, use iperf3 -B so traffic uses the UE PDU address (see subscriber static IP)."""
    cmd = [
        "iperf3",
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
    if bind_ip:
        cmd.extend(["-B", bind_ip])
    if reverse:
        cmd.append("-R")
    if udp:
        cmd.append("-u")
        if bitrate:
            cmd.extend(["-b", bitrate])
    if parallel > 1:
        cmd.extend(["-P", str(parallel)])
    r = docker_exec(ue_container, cmd, timeout=duration + 90)
    if artifact_path is not None:
        write_iperf_artifact(
            artifact_path,
            container=ue_container,
            server=server,
            port=port,
            duration=duration,
            reverse=reverse,
            bind_ip=bind_ip,
            r=r,
            log=log,
            udp=udp,
            parallel=parallel,
            bitrate=bitrate,
            iperf_server_cmd=iperf_server_cmd,
        )
    if r.returncode != 0:
        return 0.0, (r.stderr or "") + (r.stdout or "")
    try:
        mbps, _ = parse_iperf3_json(r.stdout or "{}")
        return mbps, None
    except json.JSONDecodeError as e:
        return 0.0, f"JSON: {e}"


def _iperf_listening_on_core(core_container: str, port: int) -> bool:
    r = docker_exec(core_container, ["bash", "-c", f"ss -tlnp | grep -q ':{port} ' || ss -tlnp | grep -q ':{port}'"])
    return r.returncode == 0


def _stop_iperf3_server_in_core(core_container: str, log: logging.Logger) -> None:
    """Stop iperf3 in the core container so we can restart with -B (UDP / multi-homed)."""
    docker_exec(
        core_container,
        ["bash", "-c", "pkill -x iperf3 2>/dev/null || true"],
        timeout=20,
    )
    time.sleep(0.5)
    log.info("Sent pkill -x iperf3 in %s (ignore if none)", core_container)


def _wait_iperf_tcp_port_free(
    core_container: str, port: int, *, attempts: int = 30, delay_s: float = 0.1
) -> bool:
    for _ in range(attempts):
        if not _iperf_listening_on_core(core_container, port):
            return True
        time.sleep(delay_s)
    return not _iperf_listening_on_core(core_container, port)


def iperf_ports_by_ue(base_port: int) -> dict[int, int]:
    """Stable per-UE iperf server ports in core: UE1->base, UE2->base+1, ..."""
    return {int(exp["ue"]): base_port + idx for idx, exp in enumerate(EXPECTED_UES)}


def ensure_iperf_servers_core_multi(
    core_container: str,
    ports: list[int],
    log: logging.Logger,
    *,
    bind_addr: Optional[str] = None,
) -> bool:
    """
    Step 5 helper: kill existing iperf3 servers, then start one daemon per port.
    Starts all requested servers in parallel and verifies each listener appears.
    """
    uniq_ports = sorted(set(int(p) for p in ports))
    if not uniq_ports:
        log.error("No iperf server ports requested")
        return False

    _stop_iperf3_server_in_core(core_container, log)
    for port in uniq_ports:
        if not _wait_iperf_tcp_port_free(core_container, port):
            log.error("TCP port %s still busy in %s after pkill iperf3", port, core_container)
            return False

    start_errors: dict[int, str] = {}
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def _start_one(port: int) -> None:
        cmd = ["iperf3", "-s", "-p", str(port)]
        if bind_addr:
            cmd.extend(["-B", bind_addr])
        cmd.append("-D")
        r = docker_exec(core_container, cmd, timeout=30)
        if r.returncode != 0:
            err = ((r.stderr or "") + (r.stdout or "")).strip() or "no output"
            with lock:
                start_errors[port] = err[-800:]

    for port in uniq_ports:
        t = threading.Thread(target=_start_one, args=(port,), name=f"iperf-start-{port}")
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    if start_errors:
        for port, err in sorted(start_errors.items()):
            log.error("iperf3 server failed on %s:%s: %s", core_container, port, err)
        return False

    for _ in range(30):
        if all(_iperf_listening_on_core(core_container, port) for port in uniq_ports):
            return True
        time.sleep(0.2)

    missing = [str(p) for p in uniq_ports if not _iperf_listening_on_core(core_container, p)]
    log.error(
        "iperf3 listeners missing on %s for ports: %s",
        core_container,
        ", ".join(missing) if missing else "(unknown)",
    )
    return False


def ensure_iperf_server_core(
    core_container: str,
    port: int,
    log: logging.Logger,
    *,
    bind_addr: Optional[str] = None,
) -> bool:
    """
    Ensure iperf3 -s is running inside the 5GC container (core no longer starts it in entrypoint).
    If nothing is listening on TCP port, run: iperf3 -s [-B <addr>] -p <port> -D
    Use -B with the same address UEs use as iperf3 -c (N6 / core) so UDP control + data align on multi-homed setups.
    If a server is already listening but -B is required, stop iperf3 and restart with -B (old server may lack -B).
    """
    if _iperf_listening_on_core(core_container, port):
        if bind_addr:
            log.info(
                "iperf3 already on %s:%s — restarting with -B %s for UDP/client alignment",
                core_container,
                port,
                bind_addr,
            )
            print(
                f"[iperf3] replacing existing server on {core_container}:{port} "
                f"with -B {bind_addr}"
            )
            _stop_iperf3_server_in_core(core_container, log)
            if not _wait_iperf_tcp_port_free(core_container, port):
                log.error("TCP port %s still busy in %s after pkill iperf3", port, core_container)
                print(f"[iperf3] FAILED: port {port} still in use in {core_container}")
                return False
        else:
            log.info(
                "iperf3 server already listening — container=%s TCP port=%s",
                core_container,
                port,
            )
            print(f"[iperf3] server already running — container {core_container} port {port}")
            return True

    cmd = ["iperf3", "-s", "-p", str(port)]
    if bind_addr:
        cmd.extend(["-B", bind_addr])
    cmd.append("-D")
    log.info(
        "Starting iperf3 server — docker exec %s %s",
        core_container,
        " ".join(cmd),
    )
    print(
        f"[iperf3] starting — docker exec {core_container} "
        + (" ".join(cmd))
    )
    r = docker_exec(core_container, cmd, timeout=30)
    if r.returncode != 0:
        err = ((r.stderr or "") + (r.stdout or "")).strip()
        log.error("iperf3 server failed to start in %s: %s", core_container, err[-1200:] if err else "(no output)")
        print(f"[iperf3] FAILED to start in {core_container} port {port}")
        return False

    for _ in range(20):
        time.sleep(0.25)
        if _iperf_listening_on_core(core_container, port):
            log.info(
                "iperf3 server ready — container=%s listening on TCP port=%s",
                core_container,
                port,
            )
            print(f"[iperf3] server ready — container {core_container} port {port}")
            return True

    log.error("iperf3 did not show a TCP listener on %s:%s after start", core_container, port)
    print(f"[iperf3] FAILED: no listener on {core_container} port {port}")
    return False


def iperf_server_bind_from_args(args: argparse.Namespace) -> Optional[str]:
    """Resolve iperf3 -s -B: default matches --iperf-host; 'none' disables -B."""
    raw = (getattr(args, "iperf_server_bind", None) or "").strip()
    if raw.lower() == "none":
        return None
    if raw:
        return raw
    return (args.iperf_host or "").strip() or None


def iperf_server_cmd_for_artifact(args: argparse.Namespace, bind: Optional[str]) -> str:
    """One-line comment for iperf artifacts: expected server command on the core."""
    if bind:
        return (
            f"iperf3 server ({args.core_service}): "
            f"iperf3 -s -p {args.iperf_port} -B {bind} -D"
        )
    return f"iperf3 server ({args.core_service}): iperf3 -s -p {args.iperf_port} -D"


def iperf_server_cmd_for_artifact_port(
    core_service: str,
    port: int,
    bind: Optional[str],
) -> str:
    if bind:
        return f"iperf3 server ({core_service}): iperf3 -s -p {port} -B {bind} -D"
    return f"iperf3 server ({core_service}): iperf3 -s -p {port} -D"


@dataclass
class ParallelResult:
    ue: int
    container: str
    mbps: float = 0.0
    error: Optional[str] = None


def run_parallel_iperf(
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    log: logging.Logger,
    run_dir: Optional[Path] = None,
    *,
    udp: bool = False,
    parallel: int = 1,
    bitrate: Optional[str] = None,
    iperf_server_cmd: Optional[str] = None,
    pre_ping_host: Optional[str] = None,
    port_by_ue: Optional[dict[int, int]] = None,
    server_cmd_by_ue: Optional[dict[int, str]] = None,
) -> list[ParallelResult]:
    results: list[ParallelResult] = []
    threads: list[threading.Thread] = []
    lock = threading.Lock()
    direction = "dl" if reverse else "ul"

    def worker(exp: dict[str, Any]) -> None:
        ue = int(exp["ue"])
        ue_port = int(port_by_ue.get(ue, port)) if port_by_ue else port
        ue_server_cmd = server_cmd_by_ue.get(ue) if server_cmd_by_ue else iperf_server_cmd
        art: Optional[Path] = None
        if run_dir is not None:
            name = iperf_artifact_name(
                ue, direction, "parallel", duration, udp=udp, parallel=parallel
            )
            art = run_dir / "iperf" / name
        if pre_ping_host and not ping_from_ue_via_oaitun(exp["container"], pre_ping_host, log):
            mbps, err = 0.0, f"pre-iperf ping failed to {pre_ping_host} via oaitun"
        else:
            mbps, err = iperf3_in_container(
                exp["container"],
                server,
                ue_port,
                duration,
                reverse,
                log,
                bind_ip=exp.get("ue_ipv4"),
                artifact_path=art,
                udp=udp,
                parallel=parallel,
                bitrate=bitrate,
                iperf_server_cmd=ue_server_cmd,
            )
        with lock:
            results.append(
                ParallelResult(
                    ue=int(exp["ue"]),
                    container=exp["container"],
                    mbps=mbps,
                    error=err,
                )
            )

    for exp in EXPECTED_UES:
        t = threading.Thread(target=worker, args=(exp,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    results.sort(key=lambda x: x.ue)
    return results


def spearman_rho(xs: list[float], ys: list[float]) -> float:
    """Spearman correlation in [-1,1]; simple O(n^2) ranks."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0

    def ranks(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        r = [0.0] * n
        for rank, (idx, _) in enumerate(indexed, start=1):
            r[idx] = float(rank)
        return r

    rx = ranks(xs)
    ry = ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    denx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    deny = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if denx < 1e-9 or deny < 1e-9:
        return 0.0
    return num / (denx * deny)


def _slice_sd_to_ue_index(sd: Any) -> Optional[int]:
    """Map Slices row `sd` to UE index 1..5 for sd 0x000001..0x000005; else None."""
    if sd is None:
        return None
    if isinstance(sd, int):
        sd_int = sd & 0xFFFFFF
    elif isinstance(sd, str) and sd.startswith("0x"):
        sd_int = int(sd, 16)
    else:
        try:
            sd_int = int(sd, 16) if isinstance(sd, str) else int(sd)
        except (TypeError, ValueError):
            return None
    if 0x000001 <= sd_int <= 0x000005:
        return sd_int
    return None


def load_prb_ratios_by_ue_from_yaml(
    path: Path, log: logging.Logger
) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
    """
    Read gNB YAML `Slices`: min_prb_ratio, max_prb_ratio, dedicated_prb_ratio per UE 1..5
    (rows with sd 0x000001 .. 0x000005, same mapping as EXPECTED_UES).
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        log.warning("PyYAML not installed; using built-in defaults for slice PRB ratios")
        return (
            dict(DEFAULT_MIN_PRB_BY_UE),
            dict(DEFAULT_MAX_PRB_BY_UE),
            dict(DEFAULT_DEDICATED_PRB_BY_UE),
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    slices = data.get("Slices") or []
    min_out: dict[int, float] = {}
    max_out: dict[int, float] = {}
    ded_out: dict[int, float] = {}
    for row in slices:
        if not isinstance(row, dict):
            continue
        ue_idx = _slice_sd_to_ue_index(row.get("sd"))
        if ue_idx is None:
            continue
        if row.get("min_prb_ratio") is not None:
            min_out[ue_idx] = float(row["min_prb_ratio"])
        if row.get("max_prb_ratio") is not None:
            max_out[ue_idx] = float(row["max_prb_ratio"])
        if row.get("dedicated_prb_ratio") is not None:
            ded_out[ue_idx] = float(row["dedicated_prb_ratio"])
    if len(min_out) < 5:
        min_out = dict(DEFAULT_MIN_PRB_BY_UE)
    if len(max_out) < 5:
        max_out = dict(DEFAULT_MAX_PRB_BY_UE)
    if len(ded_out) < 5:
        ded_out = dict(DEFAULT_DEDICATED_PRB_BY_UE)
    return min_out, max_out, ded_out


def load_min_prb_from_yaml(path: Path, log: logging.Logger) -> dict[int, float]:
    m, _, _ = load_prb_ratios_by_ue_from_yaml(path, log)
    return m


def load_max_prb_from_yaml(path: Path, log: logging.Logger) -> dict[int, float]:
    """Load max_prb_ratio (%) per UE from Slices sd 0x000001..0x000005."""
    _, mx, _ = load_prb_ratios_by_ue_from_yaml(path, log)
    return mx


def load_dedicated_prb_from_yaml(path: Path, log: logging.Logger) -> dict[int, float]:
    """Load dedicated_prb_ratio (%) per UE from Slices sd 0x000001..0x000005."""
    _, _, d = load_prb_ratios_by_ue_from_yaml(path, log)
    return d


def load_scheduler_types_from_yaml(path: Path, log: logging.Logger) -> tuple[int, int]:
    """
    Load (dl_scheduler_type, ul_scheduler_type) from MACRLCs[0] in gNB YAML.
    Returns (1, 1) when unavailable to preserve current behavior.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        log.warning("PyYAML not installed; assume dl_scheduler_type=1, ul_scheduler_type=1 for max_prb check")
        return 1, 1
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        macrlcs = data.get("MACRLCs")
        if isinstance(macrlcs, list) and len(macrlcs) > 0 and isinstance(macrlcs[0], dict):
            d = macrlcs[0]
            dl = int(d.get("dl_scheduler_type", 1))
            ul = int(d.get("ul_scheduler_type", 1))
            return dl, ul
    except Exception as e:
        log.warning("Failed to parse scheduler types from %s: %s", path, e)
    return 1, 1


def sequential_max_prb_ratio_table(
    seq_ul_mbps: dict[int, float],
    seq_dl_mbps: dict[int, float],
    max_prb_by_ue: dict[int, float],
    tolerance: float,
    mode: str,
    log: logging.Logger,
) -> tuple[bool, dict[str, Any]]:
    """
    T_ref = max sequential Mbps among UEs with max_prb_ratio >= 99% (uncapped line rate).
    For each UE: expected Mbps = T_ref * (max_prb/100); normalized actual = actual/T_ref
    should match max_prb/100 within tolerance (absolute on 0..1 scale).
    """
    if mode == "dl":
        max_seq = {ue: seq_dl_mbps.get(ue, 0.0) for ue in range(1, 6)}
        mode_label = "DL-only (ul_scheduler_type=SCHE_PF)"
    elif mode == "ul":
        max_seq = {ue: seq_ul_mbps.get(ue, 0.0) for ue in range(1, 6)}
        mode_label = "UL-only (dl_scheduler_type=SCHE_PF)"
    else:
        max_seq = {ue: max(seq_ul_mbps.get(ue, 0.0), seq_dl_mbps.get(ue, 0.0)) for ue in range(1, 6)}
        mode_label = "max(UL,DL)"
    full_cap = [max_seq[ue] for ue in range(1, 6) if max_prb_by_ue.get(ue, 0.0) >= 99.0]
    if full_cap:
        t_ref = max(full_cap)
        t_ref_note = "max sequential Mbps among UEs with max_prb_ratio>=99%"
    else:
        t_ref = max(max_seq.values()) if max_seq else 0.0
        t_ref_note = "fallback: max sequential Mbps over all UEs (no max_prb_ratio>=99% in YAML)"

    rows_out: list[dict[str, Any]] = []
    all_ok = True
    if t_ref < 1e-9:
        log.error("max_prb ratio check: T_ref throughput is zero")
        return False, {
            "t_ref_mbps": 0.0,
            "t_ref_note": t_ref_note,
            "rows": [],
            "pass": False,
            "tolerance": tolerance,
        }

    lines = [
        "",
        "Sequential max throughput vs gNB max_prb_ratio (single-UE tests; no contention)",
        f"  Throughput basis = {mode_label}",
        f"  T_ref = {t_ref:.4f} Mbps — {t_ref_note}",
        f"  Expected Mbps per UE ≈ T_ref × (max_prb_ratio/100); normalized actual = actual / T_ref",
        f"  Verdict: |normalized_actual − max_prb/100| < {tolerance:.3f}",
        "",
        f"{'UE':>4}  {'max_prb%':>8}  {'actual_Mbps':>12}  {'expected_Mbps':>14}  {'norm_actual':>10}  {'norm_cfg':>8}  {'|err|':>8}  verdict",
        "  " + "-" * 96,
    ]

    for ue in range(1, 6):
        mpr = max_prb_by_ue.get(ue, DEFAULT_MAX_PRB_BY_UE.get(ue, 100.0))
        actual = max_seq[ue]
        norm_cfg = mpr / 100.0
        expected = t_ref * norm_cfg
        norm_actual = actual / t_ref
        err = abs(norm_actual - norm_cfg)
        ok = err < tolerance
        all_ok = all_ok and ok
        verdict = "OK" if ok else "CHECK"
        rows_out.append(
            {
                "ue": ue,
                "max_prb_ratio_percent": round(mpr, 3),
                "actual_max_sequential_mbps": round(actual, 6),
                "expected_mbps": round(expected, 6),
                "normalized_actual": round(norm_actual, 6),
                "normalized_config": round(norm_cfg, 6),
                "abs_err": round(err, 6),
                "verdict": verdict,
            }
        )
        lines.append(
            f"{ue:4d}  {mpr:8.1f}  {actual:12.4f}  {expected:14.4f}  {norm_actual:10.4f}  {norm_cfg:8.4f}  {err:8.4f}  {verdict}"
        )

    lines.append("  " + "-" * 96)
    block = "\n".join(lines)
    print(block)
    log.debug("max_prb sequential table:\n%s", block)
    if all_ok:
        log.info("max_prb sequential ratio check PASS (tolerance=%.4f)", tolerance)
    else:
        log.warning("max_prb sequential ratio check: some UEs outside tolerance (see table)")

    payload = {
        "t_ref_mbps": round(t_ref, 6),
        "t_ref_note": t_ref_note,
        "throughput_basis": mode_label,
        "tolerance": tolerance,
        "pass": all_ok,
        "rows": rows_out,
    }
    return all_ok, payload


def normalized_min_prb_shares(min_prb_by_ue: dict[int, float]) -> dict[int, float]:
    """Normalize min_prb_ratio (UE 1..5) to shares that sum to 1.0."""
    s = sum(min_prb_by_ue.get(i, 0.0) for i in range(1, 6))
    if s < 1e-12:
        return {i: 0.2 for i in range(1, 6)}
    return {i: min_prb_by_ue.get(i, 0.0) / s for i in range(1, 6)}


def check_parallel_dl_share_vs_min_prb(
    results: list[ParallelResult],
    min_prb_by_ue: dict[int, float],
    tolerance: float,
    log: logging.Logger,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Compare each UE's share of total parallel-DL throughput to YAML min_prb_ratio (normalized).
    Requires all 5 UEs with successful iperf; otherwise skips with ok=True and a note.
    """
    exp_share = normalized_min_prb_shares(min_prb_by_ue)
    ok_res = [p for p in results if p.error is None]
    if len(ok_res) < 5:
        msg = f"share check FAIL — need 5/5 successful parallel DL UEs, got {len(ok_res)}"
        log.error("min_prb share: %s", msg)
        return False, msg, {"skipped": False, "reason": "incomplete_results", "ok_ues": len(ok_res)}

    total_mbps = sum(p.mbps for p in ok_res)
    if total_mbps < 1e-9:
        return False, "zero total Mbps in parallel DL", {"skipped": False}

    per_ue: dict[str, Any] = {}
    max_abs_err = 0.0
    for p in ok_res:
        obs = p.mbps / total_mbps
        exp = exp_share.get(p.ue, 0.0)
        err = abs(obs - exp)
        max_abs_err = max(max_abs_err, err)
        per_ue[str(p.ue)] = {
            "mbps": round(p.mbps, 6),
            "observed_share": round(obs, 6),
            "expected_share_from_min_prb": round(exp, 6),
            "abs_err": round(err, 6),
        }

    ok = max_abs_err <= tolerance
    msg = (
        f"max|obs-exp|={max_abs_err:.4f} (tolerance={tolerance:.4f}); "
        f"{'PASS' if ok else 'FAIL'}"
    )
    if ok:
        log.info("min_prb share check PASS: %s", msg)
    else:
        log.error("min_prb share check FAIL: %s", msg)
    detail = {
        "skipped": False,
        "tolerance": tolerance,
        "max_abs_error": max(max_abs_err, 0.0),
        "per_ue": per_ue,
        "expected_shares": {str(i): round(exp_share[i], 6) for i in range(1, 6)},
    }
    return ok, msg, detail


def write_throughput_summary(path: Path, payload: dict[str, Any], log: logging.Logger) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Throughput summary written: %s", path)


def strict_relative_check(
    parallel: list[ParallelResult],
    min_prb_by_ue: dict[int, float],
    log: logging.Logger,
    ratio_floor: float,
) -> tuple[bool, str]:
    """Expect UE with highest min_prb_ratio to beat median of others (used for Step 8 sequential DL)."""
    mbps_by_ue = {p.ue: p.mbps for p in parallel if p.error is None}
    if len(mbps_by_ue) < 3:
        return False, "Not enough successful UE results for relative check."
    # UE2 has min 40% in default config
    best_ue = max(min_prb_by_ue.keys(), key=lambda u: min_prb_by_ue.get(u, 0.0))
    others = [mbps_by_ue[u] for u in mbps_by_ue if u != best_ue]
    if not others:
        return True, "single UE"

    med = statistics.median(others)
    val = mbps_by_ue.get(best_ue, 0.0)
    ok = val >= med * ratio_floor
    msg = f"UE{best_ue} (highest min_prb) throughput={val:.3f} Mbps vs median(others)={med:.3f} (floor={ratio_floor})"
    if not ok:
        log.error("strict-relative FAIL: %s", msg)
    else:
        log.info("strict-relative PASS: %s", msg)
    return ok, msg


# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------

def setup_logging(log_dir: Path, verbose: bool) -> tuple[logging.Logger, Path]:
    """
    Create a dedicated directory per run: log_dir / e2e_slice_<timestamp> /
    Main orchestrator log: e2e_slice.log; subprocess transcript: commands.log.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = log_dir / f"e2e_slice_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "e2e_slice.log"
    cmd_log = run_dir / "commands.log"
    cmd_log.write_text(
        f"# e2e_nw_slice_docker — subprocess output (docker, compose, docker exec)\n"
        f"# started {datetime.now().isoformat(timespec='seconds')}\n\n",
        encoding="utf-8",
    )
    set_command_log_file(cmd_log)
    logger = logging.getLogger("e2e_nw_slice")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.debug("Run directory: %s", run_dir)
    logger.debug("Main log file: %s", log_file)
    logger.debug("Command output log: %s", cmd_log)
    return logger, run_dir


def iperf_artifact_name(
    ue: int,
    direction: str,
    mode: str,
    duration: int,
    *,
    udp: bool = False,
    parallel: int = 1,
) -> str:
    """Filename: iperf_ue{ue}_{ul|dl}_{sequential|parallel}_t{duration}s[_udp_P{n}].txt"""
    tag = f"_udp_P{parallel}" if udp else ("_tcp" if parallel > 1 else "")
    return f"iperf_ue{ue}_{direction}_{mode}_t{duration}s{tag}.txt"


def write_iperf_artifact(
    path: Path,
    *,
    container: str,
    server: str,
    port: int,
    duration: int,
    reverse: bool,
    bind_ip: Optional[str],
    r: subprocess.CompletedProcess,
    log: logging.Logger,
    udp: bool = False,
    parallel: int = 1,
    bitrate: Optional[str] = None,
    iperf_server_cmd: Optional[str] = None,
) -> None:
    """Save raw iperf3 stdout (JSON) plus stderr/exit metadata for debugging."""
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = ""
    if udp:
        extra += " -u"
        if bitrate:
            extra += f" -b {bitrate}"
    if parallel > 1:
        extra += f" -P {parallel}"
    lines = [
        f"# container={container}",
    ]
    if iperf_server_cmd:
        lines.append(f"# {iperf_server_cmd}")
    lines.extend(
        [
            f"# iperf3 -c {server} -p {port} -t {duration} -J{' -R' if reverse else ''}"
            + extra
            + (f" -B {bind_ip}" if bind_ip else ""),
            f"# exit_code={r.returncode}",
        ]
    )
    err = (r.stderr or "").strip()
    if err:
        lines.append("# --- stderr ---")
        lines.append(err)
    lines.append("# --- stdout (iperf3 -J) ---")
    lines.append((r.stdout or "").rstrip())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.debug("iperf artifact: %s", path)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="E2E Docker network slicing test (Open5GS + gNB + 5 UEs)")
    ap.add_argument("--core-compose", type=Path, default=DEFAULT_CORE_COMPOSE)
    ap.add_argument("--core-service", default="nws-5gc")
    ap.add_argument("--ran-compose", type=Path, default=DEFAULT_RAN_COMPOSE)
    ap.add_argument("--with-flexric", action="store_true", help="Include nws-nearRT-RIC (starts after gNB)")
    ap.add_argument("--skip-start", action="store_true", help="Do not docker compose up; only verify + tests")
    ap.add_argument(
        "--skip-gnb-rebuild",
        action="store_true",
        help="Skip pre-start gNB binary rebuild in mounted workspace (faster, but may use stale nr-softmodem)",
    )
    # Always down-before-up for clean pre-test RAN state.
    # Keep legacy flags hidden to avoid breaking old wrappers; they are ignored.
    ap.add_argument("--ran-compose-down-first", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-ran-compose-down-first", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--core-timeout", type=float, default=180.0)
    ap.add_argument("--ran-timeout", type=float, default=300.0)
    ap.add_argument("--iperf-host", default="10.47.0.2", help="iperf3 server IP (N6 / core; use -B UE IP from subscriber DB)")
    ap.add_argument(
        "--l3-ping-host",
        default="10.45.0.1",
        help="ICMP target for Step 6 via oaitun (UPF/ogstun peer; not Docker bridge)",
    )
    ap.add_argument(
        "--pdu-attach-wait-attempts",
        type=int,
        default=36,
        help="Max polls per UE for expected oaitun IPv4 (attach wait before L3 ping)",
    )
    ap.add_argument(
        "--pdu-attach-wait-interval",
        type=float,
        default=5.0,
        help="Seconds between oaitun IP checks",
    )
    ap.add_argument("--iperf-port", type=int, default=5201, help="Base iperf3 port in 5GC (UE1=base ... UE5=base+4)")
    ap.add_argument("--time", "-t", type=int, default=20, dest="duration", help="iperf duration seconds")
    ap.add_argument(
        "--iperf-udp",
        action="store_true",
        help="Use UDP iperf3 (-u -b; default: TCP with -P)",
    )
    ap.add_argument(
        "--iperf-parallel",
        type=int,
        default=5,
        metavar="N",
        help="iperf3 -P parallel streams per UE client (default: 5)",
    )
    ap.add_argument(
        "--iperf-bitrate",
        default="10M",
        metavar="RATE",
        help="With --iperf-udp: -b per parallel stream (default 10M); ignored for TCP",
    )
    ap.add_argument(
        "--iperf-server-bind",
        default="",
        metavar="ADDR",
        help="iperf3 server -B bind address (default: same as --iperf-host; use 'none' to omit -B)",
    )
    ap.add_argument("--min-mbps", type=float, default=0.05, help="Sequential test pass threshold")
    ap.add_argument(
        "--traffic-direction",
        choices=("both", "dl", "ul"),
        default="both",
        help="Traffic direction to test in sequential steps: both (default), dl-only, or ul-only",
    )
    ap.add_argument(
        "--dl",
        action="store_true",
        help="When DL tests are enabled, also run Step 9 parallel DL contention test (iperf3 -R)",
    )
    ap.add_argument(
        "--strict-relative",
        action="store_true",
        help="Step 8 sequential DL: highest min_prb UE vs median others (throughput share)",
    )
    ap.add_argument("--relative-floor", type=float, default=0.75, help="min throughput ratio vs median for strict-relative")
    ap.add_argument("--gnb-yaml", type=Path, default=DEFAULT_GNB_YAML, help="For min_prb_ratio / share check vs throughput")
    ap.add_argument(
        "--skip-min-prb-share-check",
        action="store_true",
        help="Skip parallel-DL throughput share vs gNB min_prb_ratio (default: run when --dl)",
    )
    ap.add_argument(
        "--min-prb-share-tolerance",
        type=float,
        default=0.22,
        metavar="T",
        help="PASS if max_ue |obs_share-exp_share|<=T from YAML min_prb (parallel DL only)",
    )
    ap.add_argument(
        "--max-prb-ratio-tolerance",
        type=float,
        default=0.10,
        metavar="T",
        help="Sequential UL/DL: PASS if |actual/T_ref − max_prb/100|<T (0–1 scale; vs gNB max_prb_ratio)",
    )
    ap.add_argument(
        "--skip-max-prb-ratio-check",
        action="store_true",
        help="Print Step 8b table but do not fail OVERALL on max_prb_ratio vs sequential throughput",
    )
    ap.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help="Parent directory; each run creates e2e_slice_<timestamp>/ with e2e_slice.log, commands.log, container_logs_stream/, container_logs/, iperf/, throughput_summary.json",
    )
    ap.add_argument(
        "--health-check-interval",
        type=float,
        default=20.0,
        metavar="SEC",
        help="Background thread: poll container running/health every SEC (0=disable)",
    )
    ap.add_argument(
        "--health-check-during-pdu",
        action="store_true",
        help="Start health thread before Step 4 (default: start after PDU attach to avoid Docker inspect/exec contention)",
    )
    ap.add_argument(
        "--stop-after-step4",
        action="store_true",
        help="Stop after Step 4 (containers up + PDU attach), skipping Steps 5-9 throughput checks",
    )
    ap.add_argument(
        "--stop-after-step5",
        action="store_true",
        help="Stop after Step 5 (iperf servers started), skipping Steps 7-9; then idle so "
        "container_logs_stream/ keeps receiving docker logs -f until Ctrl+C or SIGTERM",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    iperf_parallel = max(1, int(args.iperf_parallel))
    iperf_udp = bool(args.iperf_udp)
    iperf_bitrate: Optional[str] = args.iperf_bitrate if iperf_udp else None

    log, run_dir = setup_logging(args.log_dir, args.verbose)
    if args.ran_compose_down_first or args.no_ran_compose_down_first:
        log.info("RAN pre-test down is always enabled; legacy ran-compose-down flags are ignored.")

    if not docker_available():
        log.error("Docker not available or not running.")
        return 2

    _ue_containers = [exp["container"] for exp in EXPECTED_UES]
    _containers_for_log_export = [args.core_service, GNB_CONTAINER_NAME, *_ue_containers]
    print("=" * 60)
    print("E2E network slicing (Docker) — this run’s directory:", run_dir)
    print("  e2e_slice.log — orchestrator  |  commands.log — docker/compose/exec + live [container:…]")
    print("  container_logs_stream/ — live docker logs -f per container (while this script runs)")
    print("  container_logs/ — one-time docker logs snapshot when the script exits")
    print("(iperf3 JSON/text under", run_dir / "iperf", ")")
    print("Open5GS (5GC) container:", args.core_service)
    print("gNB container:", GNB_CONTAINER_NAME)
    print("NR UE containers:", ", ".join(_ue_containers))
    _ip = (
        f"UDP -u -P {iperf_parallel} -b {iperf_bitrate}"
        if iperf_udp
        else f"TCP" + (f" -P {iperf_parallel}" if iperf_parallel > 1 else "")
    )
    _srv_b = iperf_server_bind_from_args(args)
    print(
        "iperf3:",
        f"-t {args.duration}s",
        _ip,
        "(use --iperf-udp for UDP)",
    )
    _ue_ports = iperf_ports_by_ue(args.iperf_port)
    _port_span = f"{min(_ue_ports.values())}-{max(_ue_ports.values())}"
    print("iperf3 server bind (-B):", _srv_b if _srv_b else "(none)")
    print("iperf3 server ports (UE1..UE5):", _port_span)
    print("=" * 60)
    log.info(
        "Containers: Open5GS=%s gNB=%s UEs=%s",
        args.core_service,
        GNB_CONTAINER_NAME,
        ", ".join(_ue_containers),
    )
    log.info(
        "Run directory: %s (e2e_slice.log; commands.log; container_logs_stream/; container_logs/; iperf/)",
        run_dir,
    )

    # 1) Core
    log.info("=== Step 1: Open5GS core ===")
    if not ensure_core(args.core_compose, args.core_service, args.core_timeout, log, args.skip_start):
        export_container_logs(run_dir, _containers_for_log_export, log, label="step1_core_failed")
        return 1
    print("[Step 1] Core: PASS —", args.core_service, "running")

    # 2) MongoDB subscribers
    log.info("=== Step 2: MongoDB subscribers (5 UEs, SST/SD) ===")
    sub_ok, sub_lines = verify_subscribers(args.core_service, log)
    for line in sub_lines:
        print("[Step 2]", line)
    if not sub_ok:
        log.error("Subscriber verification failed.")
        export_container_logs(run_dir, _containers_for_log_export, log, label="step2_subscriber_failed")
        return 1

    # 3) RAN
    log.info("=== Step 3: gNB + 5 UEs ===")
    # gNB + UEs (two-phase bring-up inside ensure_ran); optional FlexRIC after
    if not ensure_ran(
        args.ran_compose,
        args.ran_timeout,
        log,
        args.skip_start,
        ran_down_first=True,
        rebuild_gnb=not args.skip_gnb_rebuild,
    ):
        export_container_logs(run_dir, _containers_for_log_export, log, label="step3_ran_failed")
        return 1
    if args.with_flexric and not args.skip_start:
        log.info("Starting FlexRIC...")
        ok, out = compose_up(args.ran_compose, ["nws-nearRT-RIC"], cwd=args.ran_compose.parent)
        if not ok:
            log.error("FlexRIC compose failed: %s", out[-1500:])
        else:
            wait_process_in_container("nws-nearRT-RIC", "nearRT-RIC", 90.0, log)
    print("[Step 3] RAN: PASS — gNB + 5 UEs processes up")

    health_ctx: dict[str, Any] = {}
    stream_ctx: dict[str, Any] = {}
    if args.health_check_interval > 0 and args.health_check_during_pdu:
        _start_container_health_thread(args, log, _ue_containers, health_ctx)
    _start_container_log_stream_thread(args, log, _ue_containers, stream_ctx, run_dir)

    try:
        return _post_ran_steps(
            args,
            log,
            _ue_containers,
            health_ctx,
            run_dir,
            iperf_parallel=iperf_parallel,
            iperf_udp=iperf_udp,
            iperf_bitrate=iperf_bitrate,
        )
    finally:
        ev = health_ctx.get("stop")
        if ev is not None:
            ev.set()
        t = health_ctx.get("thread")
        if t is not None:
            t.join(timeout=min(12.0, args.health_check_interval + 4.0))
        evs = stream_ctx.get("stop")
        if evs is not None:
            evs.set()
        ts = stream_ctx.get("thread")
        if ts is not None:
            ts.join(timeout=30.0)
        export_container_logs(run_dir, _containers_for_log_export, log, label="post_test")


def _start_container_health_thread(
    args: argparse.Namespace,
    log: logging.Logger,
    ue_containers: list[str],
    health_ctx: dict[str, Any],
) -> None:
    """Populate health_ctx with stop Event and Thread; caller must .set() stop in finally."""
    if args.health_check_interval <= 0:
        return
    stop_health = threading.Event()
    mon_names = [args.core_service, GNB_CONTAINER_NAME, *ue_containers]
    if args.with_flexric:
        mon_names.append("nws-nearRT-RIC")

    def _health_worker() -> None:
        run_container_health_loop(stop_health, mon_names, args.health_check_interval, log)

    health_thread = threading.Thread(target=_health_worker, name="container-health", daemon=True)
    health_thread.start()
    health_ctx["stop"] = stop_health
    health_ctx["thread"] = health_thread
    log.info(
        "Background health check thread every %.1fs: %s",
        args.health_check_interval,
        ", ".join(mon_names),
    )


def _start_container_log_stream_thread(
    args: argparse.Namespace,
    log: logging.Logger,
    ue_containers: list[str],
    stream_ctx: dict[str, Any],
    run_dir: Path,
) -> None:
    """Populate stream_ctx with stop Event and Thread for live container logs (files + commands.log)."""
    stop_stream = threading.Event()
    mon_names = [args.core_service, GNB_CONTAINER_NAME, *ue_containers]
    if args.with_flexric:
        mon_names.append("nws-nearRT-RIC")

    def _stream_worker() -> None:
        run_container_log_follow_streams(stop_stream, mon_names, run_dir, log)

    stream_thread = threading.Thread(target=_stream_worker, name="container-log-stream", daemon=True)
    stream_thread.start()
    stream_ctx["stop"] = stop_stream
    stream_ctx["thread"] = stream_thread
    log.info(
        "Background docker logs -f for: %s (mirror in commands.log; files under %s)",
        ", ".join(mon_names),
        run_dir / "container_logs_stream",
    )


def _post_ran_steps(
    args: argparse.Namespace,
    log: logging.Logger,
    ue_containers: list[str],
    health_ctx: dict[str, Any],
    run_dir: Path,
    *,
    iperf_parallel: int,
    iperf_udp: bool,
    iperf_bitrate: Optional[str],
) -> int:
    """Steps 4–9 after RAN is up (extracted so main() can wrap in try/finally for health thread)."""
    # 4) PDU attach: expected static IPv4 on oaitun (poll before L3 / iperf)
    log.info("=== Step 4: PDU attach (oaitun IPv4) ===")
    if not wait_all_ues_pdu_tun(
        EXPECTED_UES,
        log,
        args.pdu_attach_wait_attempts,
        args.pdu_attach_wait_interval,
    ):
        print("[Step 4] PDU attach: FAIL — expected tun IP not seen on all UEs")
        log.error(
            "PDU attach debug: docker ps -a --filter name=nws-oai; "
            "docker logs nws-oai-nr-ue1 --tail 120; "
            "docker exec nws-oai-nr-ue1 ip -4 -o addr show"
        )
        return 1
    print("[Step 4] PDU attach: PASS — expected oaitun IP on all UEs")
    if args.stop_after_step4:
        print("[Step 4] Stop requested (--stop-after-step4): skipping Steps 5-9")
        log.info("Stopping after Step 4 by request (--stop-after-step4)")
        return 0

    # Health thread defaults to starting here (after PDU) to avoid concurrent docker inspect + docker exec.
    if args.health_check_interval > 0 and health_ctx.get("thread") is None:
        _start_container_health_thread(args, log, ue_containers, health_ctx)

    # 5) iperf3 server in core (entrypoint no longer starts it)
    log.info("=== Step 5: iperf3 servers (core, one port per UE) ===")
    _srv_b = iperf_server_bind_from_args(args)
    ue_iperf_port = iperf_ports_by_ue(args.iperf_port)
    _iperf_ports = [ue_iperf_port[int(exp["ue"])] for exp in EXPECTED_UES]
    if not ensure_iperf_servers_core_multi(
        args.core_service,
        _iperf_ports,
        log,
        bind_addr=_srv_b,
    ):
        print(
            "[Step 5] iperf3: FAIL — could not start or verify 5 parallel servers on",
            args.core_service,
            f"ports {min(_iperf_ports)}-{max(_iperf_ports)}",
        )
        return 1
    _iperf_srv_cmd_by_ue = {
        ue: iperf_server_cmd_for_artifact_port(args.core_service, port, _srv_b)
        for ue, port in sorted(ue_iperf_port.items())
    }
    print(
        f"[Step 5] iperf3: PASS — 5 servers on {args.core_service} ports {min(_iperf_ports)}-{max(_iperf_ports)}"
        + (f" -B {_srv_b}" if _srv_b else "")
        + f" (UE clients: --iperf-host {args.iperf_host})"
    )
    log.info(
        "Step 5 iperf server map: %s",
        ", ".join(f"UE{ue}:port{port}" for ue, port in sorted(ue_iperf_port.items())),
    )
    if args.stop_after_step5:
        print(
            "[Step 5] PASS — skipping Steps 7-9. Keeping this process alive for live logs:\n"
            f"  • {run_dir / 'container_logs_stream'}\n"
            f"  • {run_dir / 'commands.log'} ([container:…] lines)\n"
            "Press Ctrl+C (or send SIGTERM) to exit; snapshots export to container_logs/."
        )
        log.info("Stopping automated steps after Step 5; entering idle loop for log streaming")
        _idle_until_signal_for_log_stream(log, run_dir)
        return 0

    run_ul = args.traffic_direction in ("both", "ul")
    run_dl = args.traffic_direction in ("both", "dl")
    # 6/7) Sequential iperf UL; ping check is done immediately before each iperf run.
    if run_ul:
        log.info("=== Step 7: Sequential iperf UL (smoke) ===")
    else:
        log.info("=== Step 7: Sequential iperf UL (smoke) [SKIPPED] ===")
    log.info("iperf artifacts directory: %s", run_dir / "iperf")

    seq_ok = True
    seq_ul_mbps: dict[int, float] = {}
    if run_ul:
        for exp in EXPECTED_UES:
            ue = int(exp["ue"])
            art = run_dir / "iperf" / iperf_artifact_name(
                ue, "ul", "sequential", args.duration, udp=iperf_udp, parallel=iperf_parallel
            )
            if not ping_from_ue_via_oaitun(exp["container"], args.l3_ping_host, log):
                mbps, err = 0.0, f"pre-iperf ping failed to {args.l3_ping_host} via oaitun"
            else:
                mbps, err = iperf3_in_container(
                    exp["container"],
                    args.iperf_host,
                    ue_iperf_port[ue],
                    args.duration,
                    False,
                    log,
                    bind_ip=exp.get("ue_ipv4"),
                    artifact_path=art,
                    udp=iperf_udp,
                    parallel=iperf_parallel,
                    bitrate=iperf_bitrate,
                    iperf_server_cmd=_iperf_srv_cmd_by_ue.get(ue),
                )
            seq_ul_mbps[ue] = mbps
            st = "PASS" if err is None and mbps >= args.min_mbps else "FAIL"
            if err is None and mbps < args.min_mbps:
                seq_ok = False
            if err:
                seq_ok = False
            print(f"[Step 7] Sequential UL {exp['container']}: {st} {mbps:.3f} Mbps")
            log.info("%s sequential UL: %s %.3f Mbps", exp["container"], st, mbps)
            if err:
                log.debug("%s sequential UL iperf stderr/stdout:\n%s", exp["container"], err)
    else:
        print("[Step 7] Sequential UL: skipped (--traffic-direction dl)")
        log.info("Step 7 skipped: UL sequential test disabled by --traffic-direction=%s", args.traffic_direction)

    # 8) Sequential iperf DL (-R)
    par: list[ParallelResult] = []
    par_ok = True
    if run_dl:
        log.info("=== Step 8: Sequential iperf DL (-R) ===")
        for exp in EXPECTED_UES:
            ue = int(exp["ue"])
            art = run_dir / "iperf" / iperf_artifact_name(
                ue, "dl", "sequential", args.duration, udp=iperf_udp, parallel=iperf_parallel
            )
            if not ping_from_ue_via_oaitun(exp["container"], args.l3_ping_host, log):
                mbps, err = 0.0, f"pre-iperf ping failed to {args.l3_ping_host} via oaitun"
            else:
                mbps, err = iperf3_in_container(
                    exp["container"],
                    args.iperf_host,
                    ue_iperf_port[ue],
                    args.duration,
                    True,
                    log,
                    bind_ip=exp.get("ue_ipv4"),
                    artifact_path=art,
                    udp=iperf_udp,
                    parallel=iperf_parallel,
                    bitrate=iperf_bitrate,
                    iperf_server_cmd=_iperf_srv_cmd_by_ue.get(ue),
                )
            st = "PASS" if err is None and mbps >= args.min_mbps else "FAIL"
            if err is None and mbps < args.min_mbps:
                par_ok = False
            if err:
                par_ok = False
            print(f"[Step 8] Sequential DL {exp['container']}: {st} {mbps:.3f} Mbps")
            log.info("%s sequential DL: %s %.3f Mbps", exp["container"], st, mbps)
            if err:
                log.debug("%s sequential DL iperf stderr/stdout:\n%s", exp["container"], err)
            par.append(ParallelResult(ue=ue, container=exp["container"], mbps=mbps, error=err))
    else:
        print("[Step 8] Sequential DL: skipped (--traffic-direction ul)")
        log.info("Step 8 skipped: DL sequential test disabled by --traffic-direction=%s", args.traffic_direction)

    seq_dl_mbps = {p.ue: p.mbps for p in par}
    min_prb, max_prb_cfg, dedicated_prb_cfg = load_prb_ratios_by_ue_from_yaml(args.gnb_yaml, log)
    dl_sched_type, ul_sched_type = load_scheduler_types_from_yaml(args.gnb_yaml, log)
    if run_dl and not run_ul:
        max_prb_mode = "dl"
    elif run_ul and not run_dl:
        max_prb_mode = "ul"
    elif dl_sched_type == 1 and ul_sched_type != 1:
        max_prb_mode = "dl"
    elif ul_sched_type == 1 and dl_sched_type != 1:
        max_prb_mode = "ul"
    else:
        max_prb_mode = "both"
    max_prb_seq_ok = True
    max_prb_table_payload: dict[str, Any] = {"skipped": False}
    if run_ul or run_dl:
        max_prb_seq_ok, max_prb_table_payload = sequential_max_prb_ratio_table(
            seq_ul_mbps,
            seq_dl_mbps,
            max_prb_cfg,
            args.max_prb_ratio_tolerance,
            max_prb_mode,
            log,
        )
        if args.skip_max_prb_ratio_check:
            max_prb_table_payload["overall_gated"] = False
            print(
                f"[Step 8b] max_prb_ratio vs sequential max Mbps: table above "
                f"(OVERALL not gated — --skip-max-prb-ratio-check)"
            )
        else:
            max_prb_table_payload["overall_gated"] = True
            print(
                f"[Step 8b] max_prb_ratio vs sequential max Mbps: "
                f"{'PASS' if max_prb_seq_ok else 'FAIL'} "
                f"(|norm_actual−norm_cfg| < {args.max_prb_ratio_tolerance})"
            )
    else:
        max_prb_table_payload = {"skipped": True, "reason": "no sequential UL/DL measurements available"}
        print("[Step 8b] max_prb_ratio vs sequential max Mbps: skipped (no directional tests enabled)")

    rho: Optional[float] = None
    if run_dl:
        mbps_list = [p.mbps for p in par]
        min_list = [min_prb.get(i, DEFAULT_MIN_PRB_BY_UE[i]) for i in range(1, 6)]
        rho = spearman_rho(min_list, mbps_list)
        print(f"[Step 8] Spearman(min_prb_ratio vs Mbps DL): {rho:.3f} (informational, not pass/fail)")
        log.info("Spearman rho(min_prb, mbps_dl)=%.4f", rho)
    else:
        print("[Step 8] Spearman(min_prb_ratio vs Mbps DL): skipped (DL not tested)")
        log.info("Step 8 Spearman skipped: DL sequential test disabled")

    rel_ok = True
    if args.strict_relative and run_dl:
        rel_ok, rel_msg = strict_relative_check(par, min_prb, log, args.relative_floor)
        print("[Step 8] strict-relative:", "PASS" if rel_ok else "FAIL", "-", rel_msg)
    elif args.strict_relative and not run_dl:
        print("[Step 8] strict-relative: skipped (--strict-relative requires DL tests)")
    else:
        print("[Step 8] strict-relative: skipped (use --strict-relative)")

    par_dl: list[ParallelResult] = []
    share_ok = True
    share_detail_out: Optional[dict[str, Any]] = None

    # 9) Parallel DL optional (contention + min_prb share vs YAML)
    if run_dl and args.dl:
        log.info("=== Step 9: Parallel iperf DL (-R) ===")
        par_dl = run_parallel_iperf(
            args.iperf_host,
            args.iperf_port,
            args.duration,
            True,
            log,
            run_dir=run_dir,
            udp=iperf_udp,
            parallel=iperf_parallel,
            bitrate=iperf_bitrate,
            port_by_ue=ue_iperf_port,
            server_cmd_by_ue=_iperf_srv_cmd_by_ue,
            pre_ping_host=args.l3_ping_host,
        )
        for p in par_dl:
            st = "PASS" if p.error is None and p.mbps >= args.min_mbps else "FAIL"
            print(f"[Step 9] Parallel DL UE{p.ue}: {st} {p.mbps:.3f} Mbps")
            log.info("Parallel DL UE%d: %s %.3f Mbps", p.ue, st, p.mbps)
            if p.error:
                log.debug("Parallel DL UE%s iperf stderr/stdout:\n%s", p.ue, p.error)
        if not args.skip_min_prb_share_check:
            share_ok, share_msg, share_detail_out = check_parallel_dl_share_vs_min_prb(
                par_dl,
                min_prb,
                args.min_prb_share_tolerance,
                log,
            )
            print(
                f"[Step 9] min_prb share vs YAML (parallel DL): "
                f"{'PASS' if share_ok else 'FAIL'} — {share_msg}"
            )
        else:
            share_detail_out = {"skipped": True, "reason": "--skip-min-prb-share-check"}
            print("[Step 9] min_prb share vs YAML: skipped (--skip-min-prb-share-check)")
    elif run_dl and not args.dl:
        share_detail_out = {"skipped": True, "reason": "no --dl (parallel DL not run)"}
    else:
        share_detail_out = {"skipped": True, "reason": "DL tests disabled by --traffic-direction"}
        print("[Step 9] Parallel DL: skipped (DL not enabled)")

    # Max throughput per UE is only from sequential UL/DL (one UE on air at a time), not parallel DL.
    max_sequential_mbps_per_ue: dict[int, float] = {}
    for ue in range(1, 6):
        max_sequential_mbps_per_ue[ue] = max(
            seq_ul_mbps.get(ue, 0.0),
            seq_dl_mbps.get(ue, 0.0),
        )

    summary: dict[str, Any] = {
        "traffic_direction": args.traffic_direction,
        "run_sequential_ul": run_ul,
        "run_sequential_dl": run_dl,
        "run_parallel_dl": bool(run_dl and args.dl),
        "iperf_duration_s": args.duration,
        "iperf_udp": iperf_udp,
        "iperf_parallel": iperf_parallel,
        "iperf_bitrate": iperf_bitrate,
        "iperf_server_bind": iperf_server_bind_from_args(args),
        "iperf_server_cmd_line": f"iperf3 -s -p <UE-port> {'-B ' + _srv_b if _srv_b else ''} -D",
        "iperf_server_ports_by_ue": {str(ue): port for ue, port in sorted(ue_iperf_port.items())},
        "iperf_server_cmd_line_by_ue": {str(ue): cmd for ue, cmd in sorted(_iperf_srv_cmd_by_ue.items())},
        "gnb_yaml": str(args.gnb_yaml.resolve()),
        "max_prb_ratio_by_ue": {str(i): max_prb_cfg.get(i, DEFAULT_MAX_PRB_BY_UE[i]) for i in range(1, 6)},
        "dedicated_prb_ratio_by_ue": {
            str(i): dedicated_prb_cfg.get(i, DEFAULT_DEDICATED_PRB_BY_UE[i]) for i in range(1, 6)
        },
        "max_prb_sequential_check": max_prb_table_payload,
        "min_prb_ratio_by_ue": {str(i): min_prb.get(i, DEFAULT_MIN_PRB_BY_UE[i]) for i in range(1, 6)},
        "expected_share_from_min_prb": {
            str(i): round(normalized_min_prb_shares(min_prb)[i], 6) for i in range(1, 6)
        },
        "sequential_ul_mbps": {str(k): round(v, 6) for k, v in sorted(seq_ul_mbps.items())} if run_ul else None,
        "sequential_dl_mbps": {str(k): round(v, 6) for k, v in sorted(seq_dl_mbps.items())} if run_dl else None,
        "max_sequential_mbps_per_ue": {
            str(k): round(v, 6) for k, v in sorted(max_sequential_mbps_per_ue.items())
        },
        "spearman_min_prb_vs_dl_mbps": round(rho, 6) if rho is not None else None,
        "parallel_dl_mbps": {str(p.ue): round(p.mbps, 6) for p in par_dl} if par_dl else None,
        "share_check_parallel_dl": share_detail_out,
    }
    write_throughput_summary(run_dir / "throughput_summary.json", summary, log)
    print(
        f"Throughput summary (max sequential UL/DL Mbps + YAML share check): "
        f"{run_dir / 'throughput_summary.json'}"
    )

    overall = (
        seq_ok
        and par_ok
        and (max_prb_seq_ok if not args.skip_max_prb_ratio_check else True)
        and (rel_ok if args.strict_relative else True)
        and share_ok
    )
    print("=" * 60)
    print("OVERALL:", "PASS" if overall else "FAIL")
    print("Run logs / iperf files:", run_dir)
    print("=" * 60)
    log.info("OVERALL %s (artifacts in %s)", "PASS" if overall else "FAIL", run_dir)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())

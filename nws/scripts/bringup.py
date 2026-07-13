#!/usr/bin/env python3
"""
Bring up Open5GS 5GC + nearRT-RIC + OAI gNB + N UEs (rfsim), then verify PDU ping.

Defaults: 5 UEs, NS UL + PF DL (docker-compose.open5gs.5slices.nsul.yaml).

Examples:
  python3 bringup.py                         # NSUL; rebuilds OAI gNB if sources newer
  python3 bringup.py --ues 5 --sch NSDL
  python3 bringup.py --force-rebuild-oai     # always recompile ran-build + oai-gnb
  python3 bringup.py --no-build              # skip OAI recompile and compose --build
  python3 bringup.py --ues 2 --sch PF
  python3 bringup.py --skip-core
  python3 bringup.py --no-ric
  python3 bringup.py --no-ping
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
NWS_DIR = SCRIPT_DIR.parent
NETWORK_SLICING_DIR = NWS_DIR.parent
COMPOSE_DIR = NWS_DIR / "docker-compose"
GNB_CFG_DIR = NWS_DIR / "configs" / "gnb"
BUILD_SCRIPTS_DIR = NWS_DIR / "build_scripts"
OAI_DIR = NETWORK_SLICING_DIR / "openairinterface5g"
CORE_COMPOSE = NWS_DIR / "5gc" / "open5gs" / "docker-compose.yml"
XAPP_COMPOSE = SCRIPT_DIR / "xapp" / "docker-compose.yml"
CORE_SERVICE = "nws-5gc"
GNB_SERVICE = "nws-oai-gnb"
RIC_SERVICE = "nws-nearRT-RIC"
DEFAULT_PING_HOST = "10.45.0.1"
# Compose --build only packages ran-build:latest; these scripts recompile OAI.
BUILD_RAN_BUILD_SH = BUILD_SCRIPTS_DIR / "build_ran_build.sh"
BUILD_OAI_GNB_SH = BUILD_SCRIPTS_DIR / "build_oai_gnb.sh"

# UE index 1..5 — static PDU IPs from Open5GS subscriber DB / nrue UICC configs
UES: list[dict[str, str]] = [
    {"ue": "1", "container": "nws-oai-nr-ue1", "ipv4": "10.45.0.31"},
    {"ue": "2", "container": "nws-oai-nr-ue2", "ipv4": "10.45.0.32"},
    {"ue": "3", "container": "nws-oai-nr-ue3", "ipv4": "10.45.0.33"},
    {"ue": "4", "container": "nws-oai-nr-ue4", "ipv4": "10.45.0.34"},
    {"ue": "5", "container": "nws-oai-nr-ue5", "ipv4": "10.45.0.35"},
]

# Base RAN stack per UE count (NS-oriented Open5GS + rfsim).
# gnb_yaml is relative to configs/gnb/.
RAN_BY_UES: dict[int, tuple[str, str]] = {
    1: ("docker-compose.open5gs.1ue.yaml", "gnb.sa.band78.106prb.rfsim.oai.yaml"),
    2: ("docker-compose.open5gs.2ues.yaml", "gnb.sa.band78.106prb.rfsim.oai.yaml"),
    3: (
        "docker-compose.open5gs.3slices.nsul.yaml",
        "gnb.sa.band78.106prb.rfsim.open5gs.3slices.nsul.yaml",
    ),
    4: (
        "docker-compose.open5gs.4slices.nsul.yaml",
        "gnb.sa.band78.106prb.rfsim.open5gs.4slices.nsul.yaml",
    ),
    5: (
        "docker-compose.open5gs.5slices.nsul.yaml",
        "gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsul.yaml",
    ),
}

# Dedicated PF stack (2 UEs + PF scheduler YAML).
PF_DEDICATED: dict[int, tuple[str, str]] = {
    2: (
        "docker-compose.open5gs.3slices.2UEs.throughput_PF.yaml",
        "gnb.sa.band78.106prb.rfsim.open5gs.3slices.2UEs.throughput_PF.yaml",
    ),
}

# Dedicated NS-DL stack when present (else patch from NSUL base).
NSDL_DEDICATED: dict[int, tuple[str, str]] = {
    5: (
        "docker-compose.open5gs.5slices.nsdl.yaml",
        "gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsdl.yaml",
    ),
}

# Canonical --sch values after alias normalization.
# NSUL: DL=PF UL=NS | NSDL: DL=NS UL=PF | NSBOTH: DL=NS UL=NS | PF: both PF
SCH_ALIASES: dict[str, str] = {
    "NS": "NSUL",
    "NSUL": "NSUL",
    "UL": "NSUL",
    "NSDL": "NSDL",
    "DL": "NSDL",
    "NSBOTH": "NSBOTH",
    "BOTH": "NSBOTH",
    "NSULDL": "NSBOTH",
    "NSDLUL": "NSBOTH",
    "PF": "PF",
}


def parse_ues(value: str) -> int:
    s = value.strip().lower().replace("-", "").replace("_", "")
    m = re.fullmatch(r"(\d+)\s*ues?", s) or re.fullmatch(r"(\d+)", s)
    if not m:
        raise argparse.ArgumentTypeError(f"invalid --ues {value!r}; use 1..5 or 1ue..5ue")
    n = int(m.group(1))
    if n not in (1, 2, 3, 4, 5):
        raise argparse.ArgumentTypeError("--ues must be 1..5")
    return n


def parse_sch(value: str) -> str:
    key = value.strip().upper().replace("-", "").replace("_", "")
    if key not in SCH_ALIASES:
        raise argparse.ArgumentTypeError(
            f"invalid --sch {value!r}; use NS/NSUL, NSDL, NSBOTH/BOTH, or PF"
        )
    return SCH_ALIASES[key]


def sch_dl_ul(sch: str) -> tuple[int, int]:
    """Return (dl_scheduler_type, ul_scheduler_type) for canonical sch."""
    if sch == "PF":
        return 0, 0
    if sch == "NSDL":
        return 1, 0
    if sch == "NSBOTH":
        return 1, 1
    # NSUL (default NS)
    return 0, 1


def setup_log(verbose: bool) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("bringup")


def run(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def docker_ok() -> bool:
    return run(["docker", "info"]).returncode == 0


def container_running(name: str) -> bool:
    r = run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return r.returncode == 0 and (r.stdout or "").strip().lower() == "true"


def container_health(name: str) -> Optional[str]:
    r = run(["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", name])
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip().lower() or None


def docker_exec(name: str, cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return run(["docker", "exec", name, *cmd], timeout=timeout)


def _collapse_streamed_lines(n_lines: int) -> None:
    """Erase streamed lines from the visible TTY (best-effort; leaves scrollback)."""
    if n_lines <= 0 or not sys.stdout.isatty():
        return
    rows = max(2, shutil.get_terminal_size(fallback=(80, 24)).lines - 1)
    n = min(n_lines, rows)
    sys.stdout.write(f"\033[{n}A\033[J")
    sys.stdout.flush()


class Step:
    """
    One bringup step: print title, stream body logs, then keep only title + result.
    """

    def __init__(self, index: int, total: int, title: str):
        self.index = index
        self.total = total
        self.title = title
        self.body_lines = 0
        self.t0 = time.monotonic()
        print(f"[{index}/{total}] {title} ...", flush=True)

    def write(self, text: str) -> None:
        if not text:
            return
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
            text += "\n"
        sys.stdout.flush()
        self.body_lines += text.count("\n")

    def finish(self, ok: bool, result: str = "") -> bool:
        # On success, collapse body + in-progress title; keep final title/result only.
        # On failure, leave body logs visible for debugging.
        if ok:
            _collapse_streamed_lines(self.body_lines + 1)
        status = "OK" if ok else "FAIL"
        detail = f" — {result}" if result else ""
        elapsed = time.monotonic() - self.t0
        print(
            f"[{self.index}/{self.total}] {self.title}: {status}{detail} ({elapsed:.0f}s)",
            flush=True,
        )
        return ok


def run_streamed(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: float = 600.0,
    step: Optional[Step] = None,
) -> tuple[bool, str]:
    """Run a command streaming stdout+stderr live into the current step body."""
    if step is not None:
        step.write(f"+ {' '.join(argv)}")
    else:
        print(f"+ {' '.join(argv)}", flush=True)

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        if step is not None:
            step.write(str(e))
        return False, str(e)

    chunks: list[str] = []
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                timed_out = True
                msg = f"[timeout after {timeout:.0f}s]\n"
                chunks.append(msg)
                if step is not None:
                    step.write(msg.rstrip("\n"))
                else:
                    sys.stdout.write(msg)
                    sys.stdout.flush()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
                break
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                chunks.append(line)
                if step is not None:
                    # Count lines via step.write path (adds newline if missing).
                    if line.endswith("\n"):
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        step.body_lines += line.count("\n")
                    else:
                        step.write(line)
                else:
                    sys.stdout.write(line)
                    sys.stdout.flush()
        if not timed_out:
            rest = proc.stdout.read() or ""
            if rest:
                chunks.append(rest)
                if step is not None:
                    if rest.endswith("\n"):
                        sys.stdout.write(rest)
                        sys.stdout.flush()
                        step.body_lines += rest.count("\n")
                    else:
                        step.write(rest)
                else:
                    sys.stdout.write(rest)
                    sys.stdout.flush()
            rc = proc.wait()
        else:
            rc = proc.returncode if proc.returncode is not None else 1
    except Exception as e:
        proc.kill()
        out = "".join(chunks) + f"\n{e}"
        if step is not None:
            step.write(str(e))
        return False, out

    out = "".join(chunks)
    ok = (not timed_out) and rc == 0
    return ok, out


def docker_image_created_epoch(image: str) -> Optional[float]:
    """Return image Created time as unix epoch, or None if missing."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "-f", "{{.Created}}", image],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    created = (r.stdout or "").strip()
    if not created:
        return None
    # Docker prints RFC3339 nano timestamps, e.g. 2026-07-10T16:06:49.692264283Z
    try:
        from datetime import datetime

        if created.endswith("Z"):
            created = created[:-1] + "+00:00"
        # trim sub-microsecond digits if present
        if "." in created:
            head, rest = created.split(".", 1)
            frac = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            frac = (frac + "000000")[:6]
            created = f"{head}.{frac}{tz}"
        return datetime.fromisoformat(created).timestamp()
    except ValueError:
        return None


def oai_sources_newer_than_image(image: str = "ran-build:latest") -> bool:
    """True if key OAI MAC sources are newer than the image (or image missing)."""
    img_ts = docker_image_created_epoch(image)
    if img_ts is None:
        return True
    watch = [
        OAI_DIR / "openair2" / "LAYER2" / "NR_MAC_gNB" / "gNB_scheduler_dlsch.c",
        OAI_DIR / "openair2" / "LAYER2" / "NR_MAC_gNB" / "gNB_scheduler_ulsch.c",
        OAI_DIR / "openair2" / "LAYER2" / "NR_MAC_gNB" / "gNB_scheduler_primitives.c",
        OAI_DIR / "openair2" / "LAYER2" / "NR_MAC_gNB" / "mac_rrc_dl_handler.c",
        OAI_DIR / "openair2" / "LAYER2" / "NR_MAC_gNB" / "slice_prb_allocator",
        OAI_DIR / "docker" / "Dockerfile.build.ubuntu",
    ]
    newest = 0.0
    for p in watch:
        if not p.exists():
            continue
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    newest = max(newest, f.stat().st_mtime)
        else:
            newest = max(newest, p.stat().st_mtime)
    return newest > img_ts


def rebuild_oai_gnb(*, step: Step, force: bool = False) -> bool:
    """
    Recompile ran-build + package oai-gnb.
    Let Docker handle image build cache autonomously.
    """
    if not BUILD_RAN_BUILD_SH.is_file() or not BUILD_OAI_GNB_SH.is_file():
        return step.finish(False, f"missing build scripts under {BUILD_SCRIPTS_DIR}")

    step.write("Running build_ran_build.sh...")
    ok, out = run_streamed(
        ["bash", str(BUILD_RAN_BUILD_SH)],
        cwd=BUILD_SCRIPTS_DIR,
        timeout=7200.0,
        step=step,
    )
    if not ok:
        step.write((out or "")[-2000:])
        return step.finish(False, "build_ran_build.sh failed")

    step.write("Running build_oai_gnb.sh...")
    ok, out = run_streamed(
        ["bash", str(BUILD_OAI_GNB_SH)],
        cwd=BUILD_SCRIPTS_DIR,
        timeout=1800.0,
        step=step,
    )
    if not ok:
        step.write((out or "")[-2000:])
        return step.finish(False, "build_oai_gnb.sh failed")
    return step.finish(True, "ran-build + oai-gnb checked/rebuilt")


def compose_up(
    compose_file: Path,
    services: list[str],
    *,
    cwd: Path,
    build: bool = False,
    no_deps: bool = False,
    timeout: Optional[float] = None,
    step: Optional[Step] = None,
) -> tuple[bool, str]:
    """docker compose up -d; stream logs into the current step."""
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "up",
        "-d",
        "--remove-orphans",
    ]
    if no_deps:
        argv.append("--no-deps")
    if build:
        argv.append("--build")
    argv.extend(services)

    if timeout is None:
        timeout = 3600.0 if build else 600.0

    return run_streamed(argv, cwd=cwd, timeout=timeout, step=step)


def compose_down(
    compose_file: Path,
    *,
    cwd: Path,
    step: Optional[Step] = None,
) -> tuple[bool, str]:
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file.resolve()),
        "down",
        "--remove-orphans",
    ]
    return run_streamed(argv, cwd=cwd, timeout=300.0, step=step)


def wait_healthy(name: str, timeout_s: float, log: logging.Logger, step: Optional[Step] = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if container_running(name):
            h = container_health(name)
            if h in ("healthy", "none", None):
                return True
            if h == "unhealthy":
                msg = f"{name} is unhealthy"
                if step is not None:
                    step.write(msg)
                else:
                    log.warning("%s", msg)
        time.sleep(1.0)
    return container_running(name)


def wait_process(
    name: str,
    pattern: str,
    timeout_s: float,
    log: logging.Logger,
    step: Optional[Step] = None,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not container_running(name):
            time.sleep(1.0)
            continue
        r = docker_exec(name, ["bash", "-c", f"pgrep -f '{pattern}' >/dev/null"])
        if r.returncode == 0:
            return True
        time.sleep(1.0)
    msg = f"process {pattern!r} not found in {name} within {timeout_s:.0f}s"
    if step is not None:
        step.write(msg)
    else:
        log.error("%s", msg)
    return False


def sched_label(v: Optional[int]) -> str:
    if v is None:
        return "?"
    return {0: "PF", 1: "NS"}.get(v, str(v))


def _yaml_scalar(text: str, key: str) -> Optional[str]:
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def _fmt_sd(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, int):
        return f"0x{val:06x}"
    return str(val)


def print_slice_config(gnb_yaml: Path, *, sch: str, ues: int) -> None:
    """Print scheduler + Slices table from the gNB YAML used for this bringup."""
    print()
    print("=== slice config ===")
    print(f"  gnb yaml : {gnb_yaml}")
    print(f"  requested: sch={sch}  ues={ues}")

    text = gnb_yaml.read_text(encoding="utf-8")
    dl_s = _yaml_scalar(text, "dl_scheduler_type")
    ul_s = _yaml_scalar(text, "ul_scheduler_type")
    legacy = _yaml_scalar(text, "scheduler_type")
    dl_i = int(dl_s) if dl_s and dl_s.isdigit() else None
    ul_i = int(ul_s) if ul_s and ul_s.isdigit() else None
    leg_i = int(legacy) if legacy and legacy.isdigit() else None
    if dl_i is not None or ul_i is not None:
        print(f"  scheduler: DL={sched_label(dl_i)}  UL={sched_label(ul_i)}")
    elif leg_i is not None:
        print(f"  scheduler: {sched_label(leg_i)} (legacy scheduler_type)")
    else:
        print("  scheduler: (not set in YAML)")

    slices: list[dict[str, str]] = []
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        raw = data.get("Slices") or []
        if isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                slices.append(
                    {
                        "slice_id": str(row.get("slice_id", "")),
                        "sst": str(row.get("sst", "")),
                        "sd": _fmt_sd(row.get("sd")),
                        "ded": str(row.get("dedicated_prb_ratio", "")),
                        "min": str(row.get("min_prb_ratio", "")),
                        "max": str(row.get("max_prb_ratio", "")),
                    }
                )
    except Exception:
        # Fallback: lightweight line parse of Slices block
        in_slices = False
        cur: dict[str, str] = {}
        for line in text.splitlines():
            if re.match(r"^Slices\s*:", line):
                in_slices = True
                continue
            if in_slices and re.match(r"^[A-Za-z]", line):
                if cur:
                    slices.append(cur)
                break
            if not in_slices:
                continue
            if re.match(r"^\s*-\s+slice_id\s*:", line):
                if cur:
                    slices.append(cur)
                cur = {"slice_id": line.split(":", 1)[1].strip()}
                continue
            m = re.match(r"^\s+(sst|sd|dedicated_prb_ratio|min_prb_ratio|max_prb_ratio)\s*:\s*(\S+)", line)
            if m and cur is not None:
                key = {
                    "sst": "sst",
                    "sd": "sd",
                    "dedicated_prb_ratio": "ded",
                    "min_prb_ratio": "min",
                    "max_prb_ratio": "max",
                }[m.group(1)]
                cur[key] = m.group(2)
        if cur:
            slices.append(cur)

    if not slices:
        print("  Slices  : (none / not present)")
        print()
        return

    print(
        f"  {'id':>4}  {'sst':>3}  {'sd':>10}  {'ded%':>6}  {'min%':>6}  {'max%':>6}"
    )
    for s in slices:
        print(
            f"  {s.get('slice_id',''):>4}  {s.get('sst',''):>3}  {s.get('sd',''):>10}  "
            f"{s.get('ded',''):>6}  {s.get('min',''):>6}  {s.get('max',''):>6}"
        )
    print()


def patch_gnb_scheduler(src: Path, dst: Path, sch: str) -> None:
    """
    Rewrite scheduler fields for PF / NSUL / NSDL / NSBOTH.
    Also set legacy scheduler_type (1 if any direction is NS, else 0).
    """
    text = src.read_text(encoding="utf-8")
    sch = sch.upper()
    dl, ul = sch_dl_ul(sch)
    legacy = 1 if (dl == 1 or ul == 1) else 0

    def repl_field(content: str, key: str, value: int) -> str:
        pat = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*)\d+\s*$", re.MULTILINE)
        if pat.search(content):
            return pat.sub(rf"\g<1>{value}", content)
        return content

    text = repl_field(text, "dl_scheduler_type", dl)
    text = repl_field(text, "ul_scheduler_type", ul)
    text = repl_field(text, "scheduler_type", legacy)

    # Legacy-only YAML: map NS* -> 1, PF -> 0.
    if "ul_scheduler_type:" not in text and "scheduler_type:" in text:
        text = repl_field(text, "scheduler_type", legacy)

    # Inject dl/ul fields if the file has neither (e.g. some PF/basic YAMLs).
    if "dl_scheduler_type:" not in text and "scheduler_type:" not in text:
        text = re.sub(
            r"(stats_max_ue:\s*\d+\s*\n)",
            rf"\1    dl_scheduler_type: {dl}\n    ul_scheduler_type: {ul}\n",
            text,
            count=1,
        )
    elif "dl_scheduler_type:" not in text and "ul_scheduler_type:" not in text:
        # Has legacy scheduler_type only — also add explicit dl/ul next to it.
        text = re.sub(
            r"(^\s*scheduler_type:\s*\d+\s*\n)",
            rf"\1    dl_scheduler_type: {dl}\n    ul_scheduler_type: {ul}\n",
            text,
            count=1,
            flags=re.MULTILINE,
        )

    dst.write_text(text, encoding="utf-8")


def write_patched_compose(src_compose: Path, dst_compose: Path, old_gnb: Path, new_gnb: Path) -> None:
    """Copy compose and remount gNB YAML to the patched file (absolute path)."""
    text = src_compose.read_text(encoding="utf-8")
    # Match relative mounts like ../configs/gnb/<name>.yaml
    rel = f"../configs/gnb/{old_gnb.name}"
    abs_new = str(new_gnb.resolve())
    if rel not in text:
        # Also try any mount of this basename
        pat = re.compile(rf"(- )([^\s]*{re.escape(old_gnb.name)}):(/opt/oai-gnb/etc/gnb\.yaml)")
        text2, n = pat.subn(rf"\1{abs_new}:\3", text, count=1)
        if n == 0:
            raise RuntimeError(f"could not find gNB mount for {old_gnb.name} in {src_compose}")
        text = text2
    else:
        text = text.replace(f"{rel}:/opt/oai-gnb/etc/gnb.yaml", f"{abs_new}:/opt/oai-gnb/etc/gnb.yaml", 1)
    dst_compose.write_text(text, encoding="utf-8")


def ensure_core(
    log: logging.Logger,
    timeout_s: float,
    skip: bool,
    step: Step,
) -> bool:
    if container_running(CORE_SERVICE) and container_health(CORE_SERVICE) in ("healthy", "none", None):
        return step.finish(True, f"{CORE_SERVICE} already running")
    if skip:
        return step.finish(False, "not running and --skip-core set")
    ok, out = compose_up(CORE_COMPOSE, [CORE_SERVICE], cwd=CORE_COMPOSE.parent, step=step)
    if not ok:
        step.write(out[-2000:] if out else "compose up failed")
        return step.finish(False, "compose up failed")
    if not wait_healthy(CORE_SERVICE, timeout_s, log, step=step):
        return step.finish(False, "not healthy")
    return step.finish(True, CORE_SERVICE)


def compose_has_service(compose_file: Path, service: str) -> bool:
    """True if compose YAML defines a top-level service key."""
    try:
        text = compose_file.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(rf"(?m)^\s*{re.escape(service)}\s*:", text))


def ensure_ric(
    *,
    ran_compose: Path,
    log: logging.Logger,
    timeout_s: float,
    build: bool = True,
    step: Step,
) -> bool:
    """
    Start nearRT-RIC before gNB so E2 setup can succeed.
    Prefer the RAN compose service when present; otherwise scripts/xapp/docker-compose.yml.
    Uses --no-deps so RAN compose depends_on gNB does not start gNB first.
    """
    if container_running(RIC_SERVICE):
        r = docker_exec(RIC_SERVICE, ["bash", "-c", "pgrep -f nearRT-RIC >/dev/null"], timeout=10)
        if r.returncode == 0:
            return step.finish(True, f"{RIC_SERVICE} already running")

    if compose_has_service(ran_compose, RIC_SERVICE):
        compose = ran_compose
        cwd = COMPOSE_DIR
        step.write(f"from RAN compose: {ran_compose.name}")
    elif XAPP_COMPOSE.is_file() and compose_has_service(XAPP_COMPOSE, RIC_SERVICE):
        compose = XAPP_COMPOSE
        cwd = XAPP_COMPOSE.parent
        step.write(f"from xApp compose: {XAPP_COMPOSE}")
    else:
        return step.finish(False, f"no {RIC_SERVICE} service in compose files")

    ok, out = compose_up(compose, [RIC_SERVICE], cwd=cwd, build=build, no_deps=True, step=step)
    if not ok:
        if "oai-flexric" in out.lower() or "pull access denied" in out.lower():
            step.write("hint: nws/build_scripts/build_oai_flexric.sh")
        return step.finish(False, "compose up failed")

    if not wait_healthy(RIC_SERVICE, min(timeout_s, 60.0), log, step=step):
        step.write("health not ready yet; checking process...")
    if not wait_process(RIC_SERVICE, "nearRT-RIC", min(timeout_s, 60.0), log, step=step):
        return step.finish(False, "process not found")
    return step.finish(True, RIC_SERVICE)


def stop_prior_ran(
    *,
    compose: Path,
    with_ric: bool,
    step: Step,
) -> bool:
    cwd = COMPOSE_DIR
    ok, out = compose_down(compose, cwd=cwd, step=step)
    if not ok:
        step.write((out or "")[-800:] or "compose down non-zero")
    if with_ric and container_running(RIC_SERVICE):
        if XAPP_COMPOSE.is_file() and not compose_has_service(compose, RIC_SERVICE):
            step.write("stopping nearRT-RIC from xApp compose")
            compose_down(XAPP_COMPOSE, cwd=XAPP_COMPOSE.parent, step=step)
    time.sleep(2)
    return step.finish(True, "stopped" if ok else "stopped (with warnings)")


def ensure_gnb(
    *,
    compose: Path,
    log: logging.Logger,
    timeout_s: float,
    build: bool,
    step: Step,
) -> bool:
    ok, out = compose_up(compose, [GNB_SERVICE], cwd=COMPOSE_DIR, build=build, step=step)
    if not ok:
        step.write((out or "")[-2000:] or "compose up failed")
        return step.finish(False, "compose up failed")
    if not wait_process(GNB_SERVICE, "nr-softmodem", min(timeout_s, 180.0), log, step=step):
        return step.finish(False, "nr-softmodem not found")
    return step.finish(True, GNB_SERVICE)


def ensure_ues(
    *,
    compose: Path,
    ue_services: list[str],
    log: logging.Logger,
    timeout_s: float,
    step: Step,
) -> bool:
    ok, out = compose_up(compose, ue_services, cwd=COMPOSE_DIR, build=False, step=step)
    if not ok:
        step.write((out or "")[-2000:] or "compose up failed")
        return step.finish(False, "compose up failed")
    for name in ue_services:
        if not wait_process(name, "nr-uesoftmodem", min(timeout_s, 180.0), log, step=step):
            return step.finish(False, f"{name}: nr-uesoftmodem not found")
    return step.finish(True, f"{len(ue_services)} UE(s)")


def wait_pdu_ip(
    container: str,
    expected: str,
    log: logging.Logger,
    attempts: int,
    interval: float,
    step: Optional[Step] = None,
) -> bool:
    for i in range(attempts):
        r = docker_exec(container, ["ip", "-4", "-o", "addr", "show"], timeout=15)
        text = r.stdout or ""
        if expected in text:
            if step is not None:
                step.write(f"PDU OK: {container} has {expected}")
            else:
                log.info("PDU OK: %s has %s", container, expected)
            return True
        if i == 0 or (i + 1) % 6 == 0 or i + 1 == attempts:
            msg = f"Waiting PDU IP {expected} on {container} ({i + 1}/{attempts})..."
            if step is not None:
                step.write(msg)
            else:
                log.info("%s", msg)
        time.sleep(interval)
    msg = f"Timeout waiting for {expected} on {container}"
    if step is not None:
        step.write(msg)
    else:
        log.error("%s", msg)
    return False


def ping_ue(container: str, host: str, log: logging.Logger, step: Optional[Step] = None) -> bool:
    for iface in ("oaitun_ue0", "oaitun_ue1"):
        r = docker_exec(
            container,
            ["ping", "-c", "2", "-W", "2", "-I", iface, host],
            timeout=20,
        )
        if r.returncode == 0:
            msg = f"ping OK: {container} -> {host} via {iface}"
            if step is not None:
                step.write(msg)
            else:
                log.info("%s", msg)
            return True
    msg = f"ping FAIL: {container} -> {host} via oaitun"
    if step is not None:
        step.write(msg)
    else:
        log.error("%s", msg)
    return False


def wait_ping(
    container: str,
    host: str,
    log: logging.Logger,
    attempts: int,
    interval: float,
    step: Optional[Step] = None,
) -> bool:
    for i in range(attempts):
        if ping_ue(container, host, log, step=step):
            return True
        if i + 1 < attempts:
            msg = f"Retry ping {container} ({i + 1}/{attempts})..."
            if step is not None:
                step.write(msg)
            else:
                log.info("%s", msg)
            time.sleep(interval)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bring up 5GC + nearRT-RIC + gNB + N UEs and verify PDU ping",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--ues",
        type=parse_ues,
        default=5,
        help="Number of UEs: 1..5 or 1ue..5ue (default 5)",
    )
    ap.add_argument(
        "--bw",
        type=int,
        choices=[106, 133],
        default=133,
        help="Bandwidth in PRBs: 106 or 133 (default 133)",
    )
    ap.add_argument(
        "--sch",
        type=parse_sch,
        default="NSUL",
        help=(
            "gNB scheduler: NS/NSUL (UL NS, default), NSDL (DL NS), "
            "NSBOTH/BOTH (DL+UL NS, unstable in rfsim — see scripts/readme.md), "
            "or PF (both PF)"
        ),
    )
    ap.add_argument("--ping-host", default=DEFAULT_PING_HOST, help="L3 ping target via oaitun (UPF)")
    ap.add_argument("--skip-core", action="store_true", help="Do not start 5GC if missing")
    ap.add_argument("--no-ric", action="store_true", help="Do not start nearRT-RIC (FlexRIC)")
    ap.add_argument("--no-down-first", action="store_true", help="Skip RAN compose down before up")
    ap.add_argument(
        "--build",
        action="store_true",
        help="Compile/recompile OAI gNB (ran-build/oai-gnb) and run compose --build",
    )
    ap.add_argument(
        "--force-rebuild-oai",
        action="store_true",
        help="Force ran-build + oai-gnb rebuild even if sources look unchanged",
    )
    ap.add_argument("--no-ping", action="store_true", help="Skip PDU attach / ping checks")
    ap.add_argument("--timeout", type=float, default=180.0, help="Per-step wait timeout (seconds)")
    ap.add_argument("--pdu-attempts", type=int, default=36, help="PDU IP poll attempts per UE")
    ap.add_argument("--ping-attempts", type=int, default=24, help="Ping retry attempts per UE")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    args.build = args.build or args.force_rebuild_oai
    args.no_build = not args.build
    sch = args.sch  # already canonical via parse_sch
    log = setup_log(args.verbose)

    if not docker_ok():
        print("FAIL: docker is not available", flush=True)
        return 1
    if not CORE_COMPOSE.is_file():
        print(f"FAIL: missing core compose: {CORE_COMPOSE}", flush=True)
        return 1

    compose_name, gnb_name = RAN_BY_UES[args.ues]
    if args.bw == 133:
        if args.ues == 5:
            compose_name = "docker-compose.open5gs.5slices.nsul.133prb.yaml"
            gnb_name = "gnb.sa.band78.133prb.rfsim.open5gs.5slices.nsul.yaml"
        else:
            raise ValueError("133 PRB configuration is only supported for 5 UEs (5 slices).")

    if sch == "PF" and args.ues in PF_DEDICATED:
        if args.bw == 133:
            raise ValueError("PF scheduler is not supported with 133 PRB configuration.")
        compose_name, gnb_name = PF_DEDICATED[args.ues]
    elif sch == "NSDL" and args.ues in NSDL_DEDICATED:
        if args.bw == 133:
            raise ValueError("NSDL scheduler is not supported with 133 PRB configuration.")
        compose_name, gnb_name = NSDL_DEDICATED[args.ues]
    compose = COMPOSE_DIR / compose_name
    gnb_src = GNB_CFG_DIR / gnb_name
    if not compose.is_file():
        print(f"FAIL: missing compose: {compose}", flush=True)
        return 1
    if not gnb_src.is_file():
        print(f"FAIL: missing gNB yaml: {gnb_src}", flush=True)
        return 1

    ue_rows = UES[: args.ues]
    ue_services = [u["container"] for u in ue_rows]
    with_ric = not args.no_ric
    do_ping = not args.no_ping
    dl_i, ul_i = sch_dl_ul(sch)

    # Count steps for [i/N] titles.
    steps_plan: list[str] = []
    if not args.no_down_first:
        steps_plan.append("Stop prior RAN")
    steps_plan.append("Start 5GC")
    if not args.no_build:
        steps_plan.append("Build OAI gNB")
    if with_ric:
        steps_plan.append("Start nearRT-RIC")
    steps_plan.append("Start gNB")
    steps_plan.append("Start UEs")
    if do_ping:
        steps_plan.append("PDU attach")
        steps_plan.append("L3 ping")
    total = len(steps_plan)
    step_i = 0

    def next_step(title: str) -> Step:
        nonlocal step_i
        step_i += 1
        return Step(step_i, total, title)

    print(
        f"Bringup: ues={args.ues} sch={sch} "
        f"(DL={sched_label(dl_i)} UL={sched_label(ul_i)}) "
        f"ric={'yes' if with_ric else 'no'} "
        f"build={'no' if args.no_build else ('force' if args.force_rebuild_oai else 'yes')} "
        f"compose={compose.name} gnb={gnb_src.name}",
        flush=True,
    )

    tmpdir: Optional[tempfile.TemporaryDirectory[str]] = None
    tmp_compose: Optional[Path] = None
    gnb_effective = gnb_src
    try:
        # Patch scheduler when the selected stack YAML does not already match.
        need_patch = False
        if sch == "PF" and args.ues not in PF_DEDICATED:
            need_patch = True
        elif sch == "NSDL" and args.ues not in NSDL_DEDICATED:
            need_patch = True
        elif sch == "NSBOTH":
            need_patch = True
        # NSUL uses RAN_BY_UES nsul stacks as-is.

        if need_patch:
            tmpdir = tempfile.TemporaryDirectory(prefix="nws-bringup-")
            patched = Path(tmpdir.name) / gnb_src.name
            patch_gnb_scheduler(gnb_src, patched, sch)
            tmp_compose = COMPOSE_DIR / f".bringup-{Path(tmpdir.name).name}.yaml"
            write_patched_compose(compose, tmp_compose, gnb_src, patched)
            compose = tmp_compose
            gnb_effective = patched
            print(
                f"{sch} patch: DL={sched_label(dl_i)} UL={sched_label(ul_i)} "
                f"via {compose.name}",
                flush=True,
            )

        if not args.no_down_first:
            if not stop_prior_ran(compose=compose, with_ric=with_ric, step=next_step("Stop prior RAN")):
                return 1

        if not ensure_core(log, args.timeout, args.skip_core, step=next_step("Start 5GC")):
            return 1

        if not args.no_build:
            if not rebuild_oai_gnb(
                step=next_step("Build OAI gNB"),
                force=args.force_rebuild_oai,
            ):
                return 1

        if with_ric:
            if not ensure_ric(
                ran_compose=compose,
                log=log,
                timeout_s=args.timeout,
                build=not args.no_build,
                step=next_step("Start nearRT-RIC"),
            ):
                return 1

        if not ensure_gnb(
            compose=compose,
            log=log,
            timeout_s=args.timeout,
            # Image already rebuilt above; compose --build only repackages ran-build.
            build=not args.no_build,
            step=next_step("Start gNB"),
        ):
            return 1

        if not ensure_ues(
            compose=compose,
            ue_services=ue_services,
            log=log,
            timeout_s=args.timeout,
            step=next_step("Start UEs"),
        ):
            return 1

        if not do_ping:
            ric_note = f", {RIC_SERVICE}" if with_ric else ""
            print(f"OK: brought up 5GC{ric_note} + gNB + {args.ues} UE(s) (sch={sch})")
            print_slice_config(gnb_effective, sch=sch, ues=args.ues)
            return 0

        step = next_step("PDU attach")
        for u in ue_rows:
            if not wait_pdu_ip(
                u["container"],
                u["ipv4"],
                log,
                attempts=args.pdu_attempts,
                interval=5.0,
                step=step,
            ):
                step.finish(False, f"{u['container']} missing {u['ipv4']}")
                return 1
        step.finish(True, f"{len(ue_rows)} UE(s)")

        step = next_step(f"L3 ping ({args.ping_host})")
        failed: list[str] = []
        for u in ue_rows:
            if not wait_ping(
                u["container"],
                args.ping_host,
                log,
                attempts=args.ping_attempts,
                interval=5.0,
                step=step,
            ):
                failed.append(u["container"])
        if failed:
            step.finish(False, ", ".join(failed))
            return 1
        step.finish(True, f"all {len(ue_rows)} UE(s)")

        print(
            f"OK: 5GC"
            + (f" + {RIC_SERVICE}" if with_ric else "")
            + f" + gNB + {args.ues} UE(s) up; all UEs ping {args.ping_host} (sch={sch})"
        )
        print_slice_config(gnb_effective, sch=sch, ues=args.ues)
        return 0
    finally:
        if tmp_compose is not None and tmp_compose.exists():
            try:
                tmp_compose.unlink()
            except OSError:
                pass
        if tmpdir is not None:
            tmpdir.cleanup()


if __name__ == "__main__":
    sys.exit(main())

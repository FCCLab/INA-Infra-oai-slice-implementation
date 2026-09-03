#!/usr/bin/env python3
"""
FlexRIC Slice SM xApp — monitor OAI NS PRB policy + REST API to change it.

Subscribes to E2 Slice SM (RAN func 145) indications (`ind.ns_policy`) and
exposes a small HTTP API to GET / SET NS dedicated/min/max ratios via
`control_ns_slice_policy`.

GET overlays the last successful SET when Slice SM indications stall (CONTROL
can keep working while the indication subscription freezes). Optional watchdog
(--resubscribe-stale) re-subscribes when indications go stale; off by default
because SUBSCRIPTION_DELETE can crash nearRT-RIC when E2 state is inconsistent.

Examples:
  python3 xapp_slice.py --print --api-port 18080
  curl -s http://192.168.201.143:18080/api/v1/slices | jq .
  curl -s -X PUT http://192.168.201.143:18080/api/v1/slices \\
    -H 'Content-Type: application/json' \\
    -d '{"slices":[{"sst":1,"sd":"0x000002","direction":"ul","dedicated":10,"min":10,"max":100}]}'
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
GUI_HTML_PATH = SCRIPT_DIR / "slice_gui.html"
# scripts/xapp -> nws
NWS_DIR = SCRIPT_DIR.parent.parent if (SCRIPT_DIR.parent.name == "scripts") else SCRIPT_DIR.parent
DEFAULT_CONF = Path(
    os.environ.get(
        "FLEXRIC_CONF",
        str(
            Path("/usr/local/etc/flexric/flexric.conf")
            if os.environ.get("NWS_XAPP_IN_DOCKER") == "1"
            else NWS_DIR / "configs" / "flexric" / "flexric.conf"
        ),
    )
)
DEFAULT_OUT = Path(os.environ.get("NWS_XAPP_OUT", "rt_slice_stats.json"))
DEFAULT_NS_OUT = Path(os.environ.get("NWS_XAPP_NS_OUT", "rt_ns_slice_policy.json"))
DEFAULT_DOCKER_IMAGE = os.environ.get("NWS_FLEXRIC_IMAGE", "oai-flexric:latest")
DEFAULT_DOCKER_NET = os.environ.get("NWS_FLEXRIC_NET", "nws-oai-rf-sim")
IN_DOCKER = os.environ.get("NWS_XAPP_IN_DOCKER") == "1"

INTERVAL_CHOICES = ("1", "2", "5", "10", "100", "1000")
DEFAULT_INTERVAL = "10"
DEFAULT_API_HOST = os.environ.get("NWS_XAPP_API_HOST", "0.0.0.0")
DEFAULT_API_PORT = int(os.environ.get("NWS_XAPP_API_PORT", "18080"))
NS_DEFAULT_SD = 0xFFFFFF


def resolve_sdk_path() -> Optional[Path]:
    """Prefer PYTHONPATH entry; else FlexRIC build; else image layout path."""
    for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if not p:
            continue
        cand = Path(p)
        if (cand / "xapp_sdk.py").is_file() or list(cand.glob("_xapp_sdk*.so")):
            return cand
    for cand in (
        Path("/usr/local/flexric/xApp/python3"),
        NWS_DIR.parent
        / "openairinterface5g"
        / "openair2"
        / "E2AP"
        / "flexric"
        / "build"
        / "examples"
        / "xApp"
        / "python3",
    ):
        if (cand / "xapp_sdk.py").is_file() or list(cand.glob("_xapp_sdk*.so")):
            return cand
    return None


def can_import_sdk() -> bool:
    sdk = resolve_sdk_path()
    if sdk is None:
        return False
    if str(sdk) not in sys.path:
        sys.path.insert(0, str(sdk))
    try:
        import xapp_sdk  # noqa: F401

        return True
    except ImportError:
        return False


def import_ric():
    sdk = resolve_sdk_path()
    if sdk is not None and str(sdk) not in sys.path:
        sys.path.insert(0, str(sdk))
    try:
        import xapp_sdk as ric  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "Cannot import xapp_sdk.\n"
            "  Default: re-run without --host (uses oai-flexric:latest).\n"
            "  Or build FlexRIC Python bindings and source configs/flexric/flexric.connection.env\n"
            f"Import error: {e}"
        ) from e
    return ric


def resolve_api_port_from_argv(argv: list[str], default: int = DEFAULT_API_PORT) -> tuple[int, bool]:
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--api-port" and i + 1 < len(argv):
            return int(argv[i + 1]), True
        if a.startswith("--api-port="):
            return int(a.split("=", 1)[1]), True
        i += 1
    return default, False


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_api_port(preferred: int, host: str = "0.0.0.0", max_tries: int = 20) -> int:
    for port in range(preferred, preferred + max_tries):
        if is_port_available(port, host):
            return port
    return preferred


def run_with_timeout(fn, timeout_sec: float, label: str) -> bool:
    """Run fn in a thread; return False if it does not finish within timeout_sec."""
    err: list[BaseException] = []

    def _run() -> None:
        try:
            fn()
        except BaseException as e:
            err.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        print(f"WARN: {label} timed out after {timeout_sec:.0f}s (RIC may be down)", file=sys.stderr)
        return False
    if err:
        raise err[0]
    return True


def run_docker_forwarding_signals(docker_argv: list[str]) -> int:
    """docker run wrapper that forwards Ctrl-C/SIGTERM to the container."""
    proc = subprocess.Popen(docker_argv)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        print("\nStopping xApp container...", flush=True)
        proc.send_signal(signal.SIGINT)
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()

def warn_ric_stack_conflicts(ric_container: str = "nws-nearRT-RIC") -> None:
    """Warn about extra RIC/xApp containers that often crash or confuse nws-nearRT-RIC."""
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"],
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return
    names = {n.strip() for n in out.splitlines() if n.strip()}
    extras = []
    if "nearRT-RIC" in names and ric_container in names:
        extras.append("nearRT-RIC (legacy host-network RIC — stop if using nws stack)")
    if "xapp-python" in names:
        extras.append("xapp-python (legacy xApp — hammers E42 setup every 5s)")
    if len([n for n in names if "xapp" in n.lower() and n != ric_container]) > 1:
        extras.append("multiple xApp containers — only one should connect to nws-nearRT-RIC")
    for msg in extras:
        print(f"WARN: {msg}", file=sys.stderr)


def parse_wait_e2_from_argv(argv: list[str], default: float = 60.0) -> float:
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--wait-e2" and i + 1 < len(argv):
            return float(argv[i + 1])
        if a.startswith("--wait-e2="):
            return float(a.split("=", 1)[1])
        i += 1
    return default


def reexec_via_docker(argv: list[str], *, conf: Path, image: str, network: str) -> int:
    if not shutil.which("docker"):
        print("ERROR: docker not found and host has no xapp_sdk", file=sys.stderr)
        return 1
    script = Path(__file__).resolve()
    conf = conf.resolve()
    if not conf.is_file():
        print(f"ERROR: FlexRIC conf not found: {conf}", file=sys.stderr)
        return 1

    skip_next = False
    forwarded: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if skip_next:
            skip_next = False
            i += 1
            continue
        if a in ("--host", "--docker", "--docker-image", "--docker-net"):
            if a in ("--docker-image", "--docker-net"):
                skip_next = True
            i += 1
            continue
        if a in ("--conf", "--out", "--ns-out") and i + 1 < len(argv):
            i += 2
            continue
        if a.startswith("--conf=") or a.startswith("--out=") or a.startswith("--ns-out="):
            i += 1
            continue
        forwarded.append(a)
        i += 1

    out_host = SCRIPT_DIR / "out"
    out_host.mkdir(parents=True, exist_ok=True)
    api_port, explicit = resolve_api_port_from_argv(argv)
    if not explicit:
        available = find_available_api_port(api_port, DEFAULT_API_HOST)
        if available != api_port:
            print(f"Port {api_port} in use — REST API will use {available}")
            api_port = available
    forwarded = strip_api_port_args(forwarded)
    ric_container = os.environ.get("NWS_NEAR_RIC_CONTAINER", "nws-nearRT-RIC")
    wait_e2 = parse_wait_e2_from_argv(argv)
    if not wait_ric_e2_before_xapp_init(timeout_s=wait_e2, ric_container=ric_container):
        print(
            "ERROR: no gNB E2 on nearRT-RIC — refusing to start xApp container.\n"
            "  Enable e2_agent in gNB YAML, then: "
            f"docker restart {ric_container} nws-oai-gnb",
            file=sys.stderr,
        )
        return 1
    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "-p",
        f"{api_port}:{api_port}",
        "-e",
        "NWS_XAPP_IN_DOCKER=1",
        "-e",
        "NWS_XAPP_RIC_E2_READY=1",
        "-e",
        f"NWS_XAPP_API_PORT={api_port}",
        "-e",
        "PYTHONPATH=/usr/local/flexric/xApp/python3",
        "-e",
        "LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib/flexric:/flexric/build/src/xApp",
        "-e",
        "FLEXRIC_CONF=/usr/local/etc/flexric/flexric.conf",
        "-v",
        f"{script}:/xapp/xapp_slice.py:ro",
        "-v",
        f"{GUI_HTML_PATH}:/xapp/slice_gui.html:ro",
        "-v",
        f"{conf}:/usr/local/etc/flexric/flexric.conf:ro",
        "-v",
        f"{out_host}:/xapp/out",
        "-w",
        "/xapp",
        image,
        "python3",
        "/xapp/xapp_slice.py",
        "--conf",
        "/usr/local/etc/flexric/flexric.conf",
        "--out",
        "/xapp/out/rt_slice_stats.json",
        "--ns-out",
        "/xapp/out/rt_ns_slice_policy.json",
        "--api-port",
        str(api_port),
        *forwarded,
    ]
    if sys.stdin.isatty() and sys.stdout.isatty():
        docker_argv.insert(3, "-it")

    print(f"No host xapp_sdk — running in {image} on network {network}")
    print("  (host Python is typically 3.10; image SDK needs 3.12)")
    return run_docker_forwarding_signals(docker_argv)


def slice_algo_name(type_id: int) -> str:
    return {1: "STATIC", 2: "NVS", 4: "EDF"}.get(type_id, f"unknown({type_id})")


def _fmt_sd(sd: Any) -> str:
    try:
        return f"0x{int(sd):06x}"
    except Exception:
        return str(sd)


def parse_sd(value: Any) -> int:
    if isinstance(value, int):
        return value
    s = str(value).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s, 10)


def _dir_name(d: Any) -> str:
    if isinstance(d, str):
        s = d.strip().lower()
        if s in ("ul", "dl"):
            return s
    try:
        di = int(d)
        if di == 0:
            return "dl"
        if di == 1:
            return "ul"
    except (TypeError, ValueError):
        pass
    return str(d).lower()


def ns_policy_to_list(ind: Any) -> list[dict[str, Any]]:
    """OAI NS PRB policy from indication (the real network slices)."""
    out: list[dict[str, Any]] = []
    policy = getattr(ind, "ns_policy", None)
    if policy is None:
        return out
    for e in policy:
        out.append(
            {
                "sst": int(e.sst),
                "sd": _fmt_sd(e.sd),
                "direction": _dir_name(e.direction),
                "dedicated": float(e.dedicated_pct),
                "min": float(e.min_pct),
                "max": float(e.max_pct),
            }
        )
    return out


def flexric_ran_to_dict(ind: Any) -> dict[str, Any]:
    """FlexRIC STATIC/NVS/EDF demo model (not OAI NS PRB policy)."""
    slice_stats: dict[str, Any] = {"RAN": {"dl": {}}, "UE": {}}

    dl_dict: dict[str, Any] = slice_stats["RAN"]["dl"]
    dl = ind.slice_stats.dl
    n_slices = int(dl.len_slices)
    dl_dict["num_of_slices"] = n_slices

    if n_slices <= 0:
        try:
            dl_dict["ue_sched_algo"] = dl.sched_name[0]
        except Exception:
            dl_dict["ue_sched_algo"] = None
        dl_dict["slice_sched_algo"] = "null"
        dl_dict["slices"] = []
    else:
        dl_dict["slices"] = []
        slice_algo = "null"
        for s in dl.slices:
            slice_algo = slice_algo_name(int(s.params.type))
            entry: dict[str, Any] = {
                "index": int(s.id),
                "label": s.label[0] if s.label else "",
                "ue_sched_algo": s.sched[0] if s.sched else "",
            }
            if slice_algo == "STATIC":
                entry["slice_algo_params"] = {
                    "pos_low": int(s.params.u.sta.pos_low),
                    "pos_high": int(s.params.u.sta.pos_high),
                }
            elif slice_algo == "NVS":
                if int(s.params.u.nvs.conf) == 0:
                    entry["slice_algo_params"] = {
                        "type": "RATE",
                        "mbps_rsvd": float(s.params.u.nvs.u.rate.u1.mbps_required),
                        "mbps_ref": float(s.params.u.nvs.u.rate.u2.mbps_reference),
                    }
                elif int(s.params.u.nvs.conf) == 1:
                    entry["slice_algo_params"] = {
                        "type": "CAPACITY",
                        "pct_rsvd": float(s.params.u.nvs.u.capacity.u.pct_reserved),
                    }
                else:
                    entry["slice_algo_params"] = {"type": "unknown"}
            elif slice_algo == "EDF":
                entry["slice_algo_params"] = {
                    "deadline": int(s.params.u.edf.deadline),
                    "guaranteed_prbs": int(s.params.u.edf.guaranteed_prbs),
                    "max_replenish": int(s.params.u.edf.max_replenish),
                }
            dl_dict["slices"].append(entry)
        dl_dict["slice_sched_algo"] = slice_algo

    ue_dict: dict[str, Any] = slice_stats["UE"]
    ue_stats = ind.ue_slice_stats
    n_ue = int(ue_stats.len_ue_slice)
    ue_dict["num_of_ues"] = n_ue
    ue_dict["ues"] = []
    if n_ue > 0:
        for u in ue_stats.ues:
            dl_id: Any = "null"
            if int(u.dl_id) >= 0 and n_slices > 0:
                dl_id = int(u.dl_id)
            ue_dict["ues"].append({"rnti": hex(int(u.rnti)), "assoc_dl_slice_id": dl_id})

    return slice_stats


def slice_ind_to_dict(ind: Any) -> dict[str, Any]:
    """Full indication: ns_policy (OAI NS) + optional FlexRIC demo RAN/UE."""
    ns = ns_policy_to_list(ind)
    flex = flexric_ran_to_dict(ind)
    return {
        "tstamp": getattr(ind, "tstamp", None),
        "ns_policy": ns,
        "slices": ns,
        "flexric": flex,
    }


class MonitorState:
    def __init__(
        self,
        *,
        out: Optional[Path],
        ns_out: Optional[Path],
        do_print: bool,
        print_flexric: bool,
        quiet: bool,
    ) -> None:
        self.out = out
        self.ns_out = ns_out
        self.do_print = do_print
        self.print_flexric = print_flexric
        self.quiet = quiet
        self.count = 0
        self.last: Optional[dict[str, Any]] = None
        self.last_ind_at: Optional[float] = None
        self._last_ns_key: Optional[str] = None
        self.lock = threading.Lock()

    def on_ind(self, ind: Any) -> None:
        data = slice_ind_to_dict(ind)
        with self.lock:
            self.count += 1
            self.last = data
            self.last_ind_at = time.monotonic()
            ns = data.get("ns_policy") or []
            count = self.count
        ns_view = {"tstamp": data.get("tstamp"), "slices": ns}

        if self.out is not None:
            self.out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if self.ns_out is not None:
            self.ns_out.write_text(json.dumps(ns_view, indent=2), encoding="utf-8")

        ns_key = json.dumps(ns, sort_keys=True)
        changed = ns_key != self._last_ns_key
        if changed:
            self._last_ns_key = ns_key

        if self.do_print:
            if self.print_flexric:
                print(json.dumps(data, indent=2), flush=True)
            elif changed or count == 1:
                print(json.dumps(ns_view, indent=2), flush=True)
        elif not self.quiet and (changed or count % 100 == 1):
            n_ns = len(ns)
            n_u = data.get("flexric", {}).get("UE", {}).get("num_of_ues", 0)
            summary = ", ".join(
                f"{e['direction']}:{e['sd']} ded={e['dedicated']:.0f}/"
                f"min={e['min']:.0f}/max={e['max']:.0f}"
                for e in ns[:8]
            )
            more = f" (+{n_ns - 8} more)" if n_ns > 8 else ""
            print(
                f"[ind #{count}] ns_slices={n_ns} ues={n_u}"
                + (f" | {summary}{more}" if summary else " | (empty ns_policy)"),
                flush=True,
            )

    def indication_age_sec(self) -> Optional[float]:
        with self.lock:
            if self.last_ind_at is None:
                return None
            return max(0.0, time.monotonic() - self.last_ind_at)

    def apply_desired(self, slices: list[dict[str, Any]]) -> None:
        """Optimistic NS policy update after a successful E2 SET.

        Slice SM indications can stall while CONTROL still works. Keep GET
        coherent by writing the desired policy into the local snapshot.
        """
        cleaned: list[dict[str, Any]] = []
        for e in slices:
            cleaned.append(
                {
                    "sst": int(e["sst"]),
                    "sd": _fmt_sd(parse_sd(e["sd"])),
                    "direction": str(e["direction"]).lower(),
                    "dedicated": float(e["dedicated"]),
                    "min": float(e["min"]),
                    "max": float(e["max"]),
                }
            )
        with self.lock:
            base = dict(self.last) if self.last else {"tstamp": None, "flexric": {}}
            # Preserve default-slice rows from the last indication if present.
            prev = list(base.get("ns_policy") or [])
            keep_default = [
                e
                for e in prev
                if parse_sd(e.get("sd", 0)) == NS_DEFAULT_SD
            ]
            # Replace non-default rows with desired SET payload.
            ns = keep_default + [
                e for e in cleaned if parse_sd(e["sd"]) != NS_DEFAULT_SD
            ]
            base["ns_policy"] = ns
            base["slices"] = ns
            base["desired_at"] = time.time()
            self.last = base

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            age = (
                None
                if self.last_ind_at is None
                else max(0.0, time.monotonic() - self.last_ind_at)
            )
            if self.last is None:
                return {
                    "tstamp": None,
                    "slices": [],
                    "indications": self.count,
                    "indication_age_sec": age,
                    "source": "none",
                }
            ns = list(self.last.get("ns_policy") or [])
            return {
                "tstamp": self.last.get("tstamp"),
                "slices": ns,
                "indications": self.count,
                "indication_age_sec": age,
                "source": "indication",
            }


def make_callback(ric: Any, state: MonitorState) -> Any:
    class SLICECallback(ric.slice_cb):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            ric.slice_cb.__init__(self)

        def handle(self, ind: Any) -> None:
            state.on_ind(ind)

    return SLICECallback()


def wait_e2_nodes(ric: Any, timeout_s: float) -> list[Any]:
    deadline = time.monotonic() + timeout_s
    last_log = 0.0
    while time.monotonic() < deadline:
        conn = list(ric.conn_e2_nodes())
        if len(conn) > 0:
            return conn
        now = time.monotonic()
        if now - last_log >= 5.0:
            left = max(0.0, deadline - now)
            print(f"  … still no E2 node (is nws-nearRT-RIC up and gNB E2 attached?) {left:.0f}s left", flush=True)
            last_log = now
        time.sleep(0.5)
    return list(ric.conn_e2_nodes())


def parse_near_ric_ip(conf: Path) -> str:
    try:
        for line in conf.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("NEAR_RIC_IP"):
                return stripped.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get("NEAR_RIC_IP", "192.168.201.142")


def ric_logs_show_e2_setup(ric_container: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.check_output(
            ["docker", "logs", "--tail", "300", ric_container],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "E2 SETUP-REQUEST rx" in out


def wait_ric_e2_before_xapp_init(*, timeout_s: float, ric_container: str) -> bool:
    """Wait for gNB E2 on nearRT-RIC before xApp init (FlexRIC asserts on E42 otherwise)."""
    if os.environ.get("NWS_XAPP_SKIP_RIC_WAIT") == "1":
        return True
    if ric_logs_show_e2_setup(ric_container):
        print(f"gNB E2 already attached on {ric_container}", flush=True)
        return True

    deadline = time.monotonic() + timeout_s
    last_log = 0.0
    print(
        f"Waiting up to {timeout_s:.0f}s for gNB E2 on {ric_container} "
        "(before xApp init — avoids nearRT-RIC crash)",
        flush=True,
    )
    while time.monotonic() < deadline:
        if ric_logs_show_e2_setup(ric_container):
            print(f"  gNB E2 attached on {ric_container}", flush=True)
            return True
        now = time.monotonic()
        if now - last_log >= 5.0:
            left = max(0.0, deadline - now)
            print(
                f"  … no gNB E2 on nearRT-RIC yet "
                f"(enable e2_agent in gNB YAML and restart gNB?) {left:.0f}s left",
                flush=True,
            )
            last_log = now
        time.sleep(1.0)
    return ric_logs_show_e2_setup(ric_container)


def available_intervals(ric: Any) -> list[str]:
    found: list[str] = []
    for ms in INTERVAL_CHOICES:
        if hasattr(ric, f"Interval_ms_{ms}"):
            found.append(ms)
    for name in dir(ric):
        if name.startswith("Interval_ms_"):
            ms = name[len("Interval_ms_") :]
            if ms not in found and ms.isdigit():
                found.append(ms)
    return found


def interval_const(ric: Any, ms: str) -> Any:
    name = f"Interval_ms_{ms}"
    if hasattr(ric, name):
        return getattr(ric, name)
    avail = available_intervals(ric)
    hint = ", ".join(avail) if avail else "(none)"
    raise SystemExit(f"xapp_sdk has no {name}; try --interval among {hint}")


# ---------------------------------------------------------------------------
# NS policy validation + E2 control
# ---------------------------------------------------------------------------


def normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each slice entry must be an object")
    direction = str(raw.get("direction", "ul")).strip().lower()
    if direction not in ("ul", "dl"):
        raise ValueError(f"direction must be 'ul' or 'dl', got {direction!r}")
    dedicated = float(raw.get("dedicated", raw.get("dedicated_pct", 0.0)))
    min_pct = float(raw.get("min", raw.get("min_pct", dedicated)))
    max_pct = float(raw.get("max", raw.get("max_pct", 100.0)))
    sst = int(raw.get("sst", 1))
    sd = parse_sd(raw.get("sd", 0))
    return {
        "sst": sst,
        "sd": _fmt_sd(sd),
        "sd_int": sd,
        "direction": direction,
        "dedicated": dedicated,
        "min": min_pct,
        "max": max_pct,
    }


def validate_entries(entries: list[dict[str, Any]]) -> list[str]:
    """Return list of error strings (empty => ok)."""
    errors: list[str] = []
    if not entries:
        errors.append("slices list is empty")
        return errors

    sum_ded: dict[str, float] = {"ul": 0.0, "dl": 0.0}
    sum_min: dict[str, float] = {"ul": 0.0, "dl": 0.0}
    for i, e in enumerate(entries):
        try:
            n = normalize_entry(e)
        except (TypeError, ValueError) as ex:
            errors.append(f"slices[{i}]: {ex}")
            continue
        if n["sd_int"] == NS_DEFAULT_SD:
            errors.append(f"slices[{i}]: sd=0xffffff (default slice) cannot be SET over E2")
        for key in ("dedicated", "min", "max"):
            v = n[key]
            if v < 0.0 or v > 100.0:
                errors.append(f"slices[{i}]: {key}={v} out of [0,100]")
        if not (n["dedicated"] <= n["min"] <= n["max"]):
            errors.append(
                f"slices[{i}]: require dedicated<=min<=max "
                f"(got {n['dedicated']}/{n['min']}/{n['max']})"
            )
        sum_ded[n["direction"]] += n["dedicated"]
        sum_min[n["direction"]] += n["min"]

    for d, total in sum_ded.items():
        if total > 100.0 + 1e-6:
            errors.append(f"sum(dedicated) for {d} is {total:.1f}% (> 100%)")
    for d, total in sum_min.items():
        if total > 100.0 + 1e-6:
            errors.append(f"sum(min) for {d} is {total:.1f}% (> 100%)")
    return errors


def parse_slices_body(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if "slices" in body:
            sl = body["slices"]
            if not isinstance(sl, list):
                raise ValueError("'slices' must be a list")
            return sl
        # single entry object
        if "sd" in body or "sst" in body:
            return [body]
    raise ValueError("body must be a list of slices, {\"slices\":[...]}, or one slice object")


class SliceController:
    # If Slice SM indications stop arriving this long, prefer last SET for merge.
    # Re-subscribe (opt-in) may call SUBSCRIPTION_DELETE and crash nearRT-RIC.
    INDICATION_STALE_SEC = 15.0

    def __init__(self, ric: Any, node_id: Any, state: MonitorState) -> None:
        self.ric = ric
        self.node_id = node_id
        self.state = state
        self.lock = threading.Lock()
        self.last_set: Optional[dict[str, Any]] = None

    @staticmethod
    def _entry_key(e: dict[str, Any]) -> tuple[int, str, str]:
        return (
            int(e["sst"]),
            _fmt_sd(parse_sd(e["sd"])),
            str(e["direction"]).lower(),
        )

    def get_slices(self) -> dict[str, Any]:
        snap = self.state.snapshot()
        with self.lock:
            last_set = dict(self.last_set) if self.last_set else None
        sent = list((last_set or {}).get("sent") or [])
        if not sent:
            return snap

        # Overlay last successful SET onto indication/desired snapshot so GET
        # stays accurate when indications stall after CONTROL ACK.
        by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
        for e in snap.get("slices") or []:
            by_key[self._entry_key(e)] = {
                "sst": int(e["sst"]),
                "sd": _fmt_sd(parse_sd(e["sd"])),
                "direction": str(e["direction"]).lower(),
                "dedicated": float(e["dedicated"]),
                "min": float(e["min"]),
                "max": float(e["max"]),
            }
        for e in sent:
            by_key[self._entry_key(e)] = {
                "sst": int(e["sst"]),
                "sd": _fmt_sd(parse_sd(e["sd"])),
                "direction": str(e["direction"]).lower(),
                "dedicated": float(e["dedicated"]),
                "min": float(e["min"]),
                "max": float(e["max"]),
            }
        age = snap.get("indication_age_sec")
        stale = age is None or float(age) >= self.INDICATION_STALE_SEC
        snap["slices"] = list(by_key.values())
        snap["desired"] = sent
        snap["source"] = "last_set" if stale else "indication+last_set"
        if stale:
            snap["note"] = (
                f"indications stale ({age if age is not None else 'none'}s); "
                "GET overlays last E2 SET"
            )
        return snap

    def _merge_base_slices(self) -> list[dict[str, Any]]:
        """Policy rows used as PATCH merge base (prefer last SET when stale)."""
        snap = self.state.snapshot()
        age = snap.get("indication_age_sec")
        stale = age is None or float(age) >= self.INDICATION_STALE_SEC
        with self.lock:
            sent = list((self.last_set or {}).get("sent") or [])
        if sent and stale:
            return sent
        if sent:
            # Even when indications are fresh, include SET rows so a PATCH does
            # not drop recently-set siblings that the indication has not echoed.
            by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
            for e in snap.get("slices") or []:
                by_key[self._entry_key(e)] = e
            for e in sent:
                by_key[self._entry_key(e)] = e
            return list(by_key.values())
        return list(snap.get("slices") or [])

    def set_slices(self, raw_entries: list[dict[str, Any]]) -> dict[str, Any]:
        errors = validate_entries(raw_entries)
        if errors:
            raise ValueError("; ".join(errors))

        normalized = [normalize_entry(e) for e in raw_entries]
        swig_vec = self.ric.SLICE_nsPolicyVector()
        for n in normalized:
            e = self.ric.swig_ns_slice_policy_entry_t()
            e.sst = n["sst"]
            e.sd = n["sd_int"]
            e.direction = 0 if n["direction"] == "dl" else 1
            e.dedicated_pct = n["dedicated"]
            e.min_pct = n["min"]
            e.max_pct = n["max"]
            swig_vec.push_back(e)

        sent = [
            {
                "sst": n["sst"],
                "sd": n["sd"],
                "direction": n["direction"],
                "dedicated": n["dedicated"],
                "min": n["min"],
                "max": n["max"],
            }
            for n in normalized
        ]
        with self.lock:
            self.ric.control_ns_slice_policy(self.node_id, swig_vec)
            self.last_set = {
                "ok": True,
                "sent": sent,
                "note": (
                    "E2 CONTROL ACK means RIC got a reply; GET overlays this "
                    "SET when Slice SM indications stall"
                ),
            }
            result = dict(self.last_set)
        # Optimistic local policy so GET matches SET immediately.
        self.state.apply_desired(sent)
        return result

    def patch_slice(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Merge one entry into current policy, then SET (excluding 0xffffff)."""
        patch = normalize_entry(raw)
        if patch["sd_int"] == NS_DEFAULT_SD:
            raise ValueError("sd=0xffffff (default slice) cannot be SET over E2")

        current = [normalize_entry(e) for e in self._merge_base_slices()]
        merged: list[dict[str, Any]] = []
        found = False
        for e in current:
            if e["sd_int"] == NS_DEFAULT_SD:
                continue  # never SET default slice
            if e["sd_int"] == patch["sd_int"] and e["direction"] == patch["direction"]:
                merged.append(patch)
                found = True
            else:
                merged.append(e)
        if not found:
            merged.append(patch)

        payload = [
            {
                "sst": e["sst"],
                "sd": e["sd"],
                "direction": e["direction"],
                "dedicated": e["dedicated"],
                "min": e["min"],
                "max": e["max"],
            }
            for e in merged
        ]
        result = self.set_slices(payload)
        result["patched"] = {
            "sst": patch["sst"],
            "sd": patch["sd"],
            "direction": patch["direction"],
            "dedicated": patch["dedicated"],
            "min": patch["min"],
            "max": patch["max"],
        }
        return result


# ---------------------------------------------------------------------------
# REST API (stdlib — no Flask) + Swagger UI
# ---------------------------------------------------------------------------

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "nws NS Slice xApp API",
        "version": "1.0.0",
        "description": (
            "Read and change OAI network-slicing PRB policy over FlexRIC E2 "
            "Slice SM (`control_ns_slice_policy`).\n\n"
            "Rules: `dedicated ≤ min ≤ max`, each in `[0, 100]`, "
            "sum(dedicated) ≤ 100% and sum(min) ≤ 100% per direction, "
            "`sd=0xffffff` cannot be SET.\n\n"
            "E2 CONTROL ACK means the RIC got a reply. GET `/api/v1/slices` "
            "overlays the last successful SET when Slice SM indications stall."
        ),
    },
    "servers": [{"url": "/", "description": "this xApp"}],
    "tags": [
        {"name": "health", "description": "Liveness"},
        {"name": "slices", "description": "OAI NS PRB policy"},
    ],
    "paths": {
        "/health": {
            "get": {
                "tags": ["health"],
                "summary": "Health check",
                "operationId": "getHealth",
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Health"}
                            }
                        },
                    }
                },
            }
        },
        "/api/v1/slices": {
            "get": {
                "tags": ["slices"],
                "summary": "Get current NS policy",
                "description": "Latest `ind.ns_policy` from Slice SM indications.",
                "operationId": "getSlices",
                "responses": {
                    "200": {
                        "description": "Current policy",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SliceSnapshot"}
                            }
                        },
                    }
                },
            },
            "put": {
                "tags": ["slices"],
                "summary": "SET NS policy list",
                "description": "Replace/set policy via E2 `control_ns_slice_policy`.",
                "operationId": "putSlices",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SliceSetRequest"},
                            "examples": {
                                "setUl": {
                                    "summary": "Set one UL slice",
                                    "value": {
                                        "slices": [
                                            {
                                                "sst": 1,
                                                "sd": "0x000002",
                                                "direction": "ul",
                                                "dedicated": 10,
                                                "min": 10,
                                                "max": 100,
                                            }
                                        ]
                                    },
                                }
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "SET accepted by xApp (check gNB for apply)",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SliceSetResult"}
                            }
                        },
                    },
                    "400": {
                        "description": "Validation error",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    },
                },
            },
            "post": {
                "tags": ["slices"],
                "summary": "SET NS policy list (alias of PUT)",
                "operationId": "postSlices",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SliceSetRequest"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "SET accepted",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SliceSetResult"}
                            }
                        },
                    },
                    "400": {
                        "description": "Validation error",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    },
                },
            },
            "patch": {
                "tags": ["slices"],
                "summary": "Merge one slice then SET",
                "description": (
                    "Merge a single entry into the current indication policy "
                    "(excluding `0xffffff`), then SET the merged list."
                ),
                "operationId": "patchSlice",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SliceEntry"},
                            "examples": {
                                "patchUl": {
                                    "summary": "Patch UL max for SD 3",
                                    "value": {
                                        "sst": 1,
                                        "sd": "0x000003",
                                        "direction": "ul",
                                        "dedicated": 0,
                                        "min": 0,
                                        "max": 40,
                                    },
                                }
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Merged SET accepted",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SliceSetResult"}
                            }
                        },
                    },
                    "400": {
                        "description": "Validation error",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    },
                },
            },
        },
        "/api/v1/last-set": {
            "get": {
                "tags": ["slices"],
                "summary": "Last SET payload",
                "operationId": "getLastSet",
                "responses": {
                    "200": {
                        "description": "Last successful SET (or empty note)",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/openapi.json": {
            "get": {
                "tags": ["health"],
                "summary": "OpenAPI 3 document",
                "operationId": "getOpenApi",
                "responses": {
                    "200": {
                        "description": "OpenAPI JSON",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/docs": {
            "get": {
                "tags": ["health"],
                "summary": "Swagger UI",
                "operationId": "getSwaggerUi",
                "responses": {"200": {"description": "HTML Swagger UI"}},
            }
        },
    },
    "components": {
        "schemas": {
            "Health": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "ok"},
                    "indications": {"type": "integer"},
                    "slices": {"type": "integer", "description": "number of NS entries"},
                },
            },
            "SliceEntry": {
                "type": "object",
                "required": ["sst", "sd", "direction"],
                "properties": {
                    "sst": {"type": "integer", "example": 1},
                    "sd": {
                        "oneOf": [{"type": "string"}, {"type": "integer"}],
                        "description": "Slice Differentiator (hex string or int)",
                        "example": "0x000002",
                    },
                    "direction": {"type": "string", "enum": ["ul", "dl"], "example": "ul"},
                    "dedicated": {"type": "number", "minimum": 0, "maximum": 100, "example": 10},
                    "min": {"type": "number", "minimum": 0, "maximum": 100, "example": 10},
                    "max": {"type": "number", "minimum": 0, "maximum": 100, "example": 100},
                },
            },
            "SliceSnapshot": {
                "type": "object",
                "properties": {
                    "tstamp": {"type": "integer", "nullable": True},
                    "slices": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/SliceEntry"},
                    },
                    "indications": {"type": "integer"},
                },
            },
            "SliceSetRequest": {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["slices"],
                        "properties": {
                            "slices": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/SliceEntry"},
                            }
                        },
                    },
                    {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/SliceEntry"},
                    },
                    {"$ref": "#/components/schemas/SliceEntry"},
                ]
            },
            "SliceSetResult": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "sent": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/SliceEntry"},
                    },
                    "patched": {"$ref": "#/components/schemas/SliceEntry"},
                    "note": {"type": "string"},
                },
            },
            "Error": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
            },
        }
    },
}

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>nws NS Slice xApp — Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css"/>
  <style>body { margin: 0; background: #fafafa; }</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
        layout: 'BaseLayout',
        tryItOutEnabled: true,
      });
    };
  </script>
</body>
</html>
"""


class SliceApiHandler(BaseHTTPRequestHandler):
    controller: Optional[SliceController] = None

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: Any, *, content_type: str = "application/json") -> None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, indent=2).encode("utf-8") + b"\n"
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if code != 204:
            self.wfile.write(data)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"

        # Docs / OpenAPI do not require controller (useful before E2 is up).
        if path in ("/docs", "/swagger"):
            self._send(200, SWAGGER_HTML, content_type="text/html; charset=utf-8")
            return
        if path in ("/gui", "/ui"):
            self._send(200, _load_gui_html(), content_type="text/html; charset=utf-8")
            return
        if path == "/openapi.json":
            self._send(200, OPENAPI_SPEC)
            return
        if path == "/api/v1/hosts":
            port = int(self.server.server_address[1])
            self._send(200, _api_hosts_payload(port))
            return
        if path in ("/", "/health"):
            ctrl = self.controller
            if ctrl is None:
                self._send(
                    200,
                    {
                        "status": "starting",
                        "note": "waiting for nearRT-RIC / E2",
                        "docs": "/docs",
                        "gui": "/gui",
                        "openapi": "/openapi.json",
                    },
                )
                return
            snap = ctrl.get_slices()
            self._send(
                200,
                {
                    "status": "ok",
                    "indications": snap.get("indications", 0),
                    "indication_age_sec": snap.get("indication_age_sec"),
                    "source": snap.get("source"),
                    "slices": len(snap.get("slices") or []),
                    "docs": "/docs",
                    "gui": "/gui",
                    "openapi": "/openapi.json",
                },
            )
            return

        ctrl = self.controller
        if ctrl is None:
            self._send(503, {"error": "controller not ready (waiting for nearRT-RIC / E2)", "docs": "/docs"})
            return
        if path == "/api/v1/slices":
            self._send(200, ctrl.get_slices())
            return
        if path == "/api/v1/last-set":
            self._send(200, ctrl.last_set or {"ok": False, "note": "no SET yet"})
            return
        self._send(
            404,
            {
                "error": "not found",
                "docs": "/docs",
                "openapi": "/openapi.json",
                "endpoints": {
                    "GET /health": "liveness",
                    "GET /docs": "Swagger UI",
                    "GET /gui": "Slice policy web UI",
                    "GET /api/v1/hosts": "Reachable API URLs on this host",
                    "GET /openapi.json": "OpenAPI 3 JSON",
                    "GET /api/v1/slices": "NS policy (indication + last SET overlay)",
                    "PUT /api/v1/slices": "SET policy list via E2 control_ns_slice_policy",
                    "PATCH /api/v1/slices": "merge one slice into current policy then SET",
                    "POST /api/v1/slices": "alias of PUT",
                },
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_set(merge=False)

    def do_POST(self) -> None:  # noqa: N802
        self._handle_set(merge=False)

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_set(merge=True)

    def _handle_set(self, *, merge: bool) -> None:
        ctrl = self.controller
        if ctrl is None:
            self._send(503, {"error": "controller not ready"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/v1/slices":
            self._send(404, {"error": "not found", "docs": "/docs"})
            return
        try:
            body = self._read_json()
            if merge:
                entries = parse_slices_body(body)
                if len(entries) != 1:
                    raise ValueError("PATCH expects exactly one slice entry")
                result = ctrl.patch_slice(entries[0])
            else:
                entries = parse_slices_body(body)
                result = ctrl.set_slices(entries)
            self._send(200, result)
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def _is_docker_bridge_ip(ip: str) -> bool:
    return ip.startswith("172.") or ip.startswith("169.254.")


def _is_excluded_gateway(ip: str) -> bool:
    return ip in (
        "10.53.1.1",
        "10.47.0.1",
        "192.168.201.1",
        "192.168.202.1",
        "192.168.122.1",
        "192.168.200.1",
    )


def _ip_kind(ip: str) -> str:
    if ip.startswith("10.1.132."):
        return "lab-preferred"
    if ip.startswith("10.1.") or ip.startswith("192.168."):
        return "lab"
    if _is_docker_bridge_ip(ip):
        return "docker-bridge"
    if ip.startswith("10.244."):
        return "k8s"
    return "other"


def _collect_host_ips() -> list[dict[str, str]]:
    """Enumerate non-loopback IPv4 addresses with interface names."""
    found: dict[str, str] = {}

    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=2.0,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1]
            addr = parts[3].split("/")[0]
            if addr and not addr.startswith("127."):
                found.setdefault(addr, iface)
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=2.0)
        for ip in out.split():
            if ip and not ip.startswith("127."):
                found.setdefault(ip, found.get(ip, ""))
    except (OSError, subprocess.SubprocessError):
        pass

    if not found:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip and not ip.startswith("127."):
                    found.setdefault(ip, "")
        except OSError:
            pass

    rows: list[dict[str, str]] = []
    for ip, iface in sorted(found.items()):
        if ip.startswith("10.244.") or _is_excluded_gateway(ip):
            continue
        rows.append({"ip": ip, "iface": iface, "kind": _ip_kind(ip)})

    def sort_key(row: dict[str, str]) -> tuple[int, str]:
        kind_order = {
            "lab-preferred": 0,
            "lab": 1,
            "other": 2,
            "docker-bridge": 3,
            "k8s": 4,
        }
        return (kind_order.get(row["kind"], 9), row["ip"])

    rows.sort(key=sort_key)
    return rows


def _api_hosts_payload(port: int) -> dict[str, Any]:
    override = (os.environ.get("NWS_XAPP_LAB_IP") or os.environ.get("NWS_XAPP_LAB_URL") or "").strip()
    if override:
        if override.startswith("http://") or override.startswith("https://"):
            base = override.rstrip("/").removesuffix("/docs").removesuffix("/gui")
        else:
            base = f"http://{override}:{port}"
        return {
            "port": port,
            "override": override,
            "base_urls": [
                {
                    "ip": override,
                    "iface": "env",
                    "kind": "override",
                    "api": f"{base}/api/v1/slices",
                    "gui": f"{base}/gui",
                    "docs": f"{base}/docs",
                }
            ],
        }

    rows = _collect_host_ips()
    base_urls = [
        {
            "ip": row["ip"],
            "iface": row["iface"],
            "kind": row["kind"],
            "api": f"http://{row['ip']}:{port}/api/v1/slices",
            "gui": f"http://{row['ip']}:{port}/gui",
            "docs": f"http://{row['ip']}:{port}/docs",
        }
        for row in rows
    ]
    if not base_urls:
        base_urls = [
            {
                "ip": "127.0.0.1",
                "iface": "loopback",
                "kind": "local",
                "api": f"http://127.0.0.1:{port}/api/v1/slices",
                "gui": f"http://127.0.0.1:{port}/gui",
                "docs": f"http://127.0.0.1:{port}/docs",
            }
        ]
    return {"port": port, "base_urls": base_urls}


def _lab_api_urls(port: int) -> list[str]:
    """Advertise reachable LAN GUI URLs (env override or auto-detect host IPv4s)."""
    payload = _api_hosts_payload(port)
    return [entry["gui"] for entry in payload.get("base_urls", [])]


def _load_gui_html() -> str:
    path = GUI_HTML_PATH if GUI_HTML_PATH.is_file() else Path("/xapp/slice_gui.html")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "<html><body><h1>slice_gui.html not found</h1></body></html>"


def _print_api_urls(host: str, port: int) -> None:
    print(f"REST API listening on http://{host}:{port}", flush=True)
    print(f"  Slice GUI   http://{host}:{port}/gui", flush=True)
    print(f"  Swagger UI  http://{host}:{port}/docs", flush=True)
    print(f"  OpenAPI     http://{host}:{port}/openapi.json", flush=True)
    if host in ("0.0.0.0", "::", ""):
        for url in _lab_api_urls(port):
            print(f"  (lab GUI)   {url}", flush=True)
    print("  GET/PUT/PATCH /api/v1/slices", flush=True)


def start_api_server(
    host: str,
    port: int,
    controller: Optional[SliceController] = None,
) -> ThreadingHTTPServer:
    SliceApiHandler.controller = controller
    httpd = ThreadingHTTPServer((host, port), SliceApiHandler)
    t = threading.Thread(target=httpd.serve_forever, name="xapp-api", daemon=True)
    t.start()
    _print_api_urls(host, port)
    return httpd


def _bootstrap_api_main(host: str, port: int) -> None:
    """Child process: serve /docs while parent FlexRIC init may hold the GIL."""
    SliceApiHandler.controller = None
    httpd = ThreadingHTTPServer((host, port), SliceApiHandler)
    httpd.serve_forever()


def start_bootstrap_api_process(host: str, port: int) -> Any:
    """Fork a docs/health server so Swagger stays up during FlexRIC connect."""
    import multiprocessing as mp

    ctx = mp.get_context("fork")
    proc = ctx.Process(target=_bootstrap_api_main, args=(host, port), daemon=True)
    proc.start()
    _print_api_urls(host, port)
    print("  (bootstrap process while connecting to nearRT-RIC)", flush=True)
    return proc


def attach_api_controller(controller: SliceController) -> None:
    SliceApiHandler.controller = controller
    print("REST API: E2 controller attached (SET/PATCH enabled)", flush=True)

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Monitor + REST-control OAI NS slice policy over FlexRIC Slice SM",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--conf",
        type=Path,
        default=Path(os.environ.get("FLEXRIC_CONF", DEFAULT_CONF)),
        help="FlexRIC/xApp conf (NEAR_RIC_IP)",
    )
    ap.add_argument("--node-idx", type=int, default=0, help="E2 node index in conn_e2_nodes()")
    ap.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help="Report period in ms (Interval_ms_*)",
    )
    ap.add_argument("--duration", type=float, default=0.0, help="Seconds to run (0 = until Ctrl-C)")
    ap.add_argument("--wait-e2", type=float, default=60.0, help="Seconds to wait for E2 node")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Full indication JSON path")
    ap.add_argument("--ns-out", type=Path, default=DEFAULT_NS_OUT, help="NS policy JSON path")
    ap.add_argument("--print", dest="do_print", action="store_true", help="Print NS policy on change")
    ap.add_argument("--print-flexric", action="store_true", help="Print full FlexRIC demo dump")
    ap.add_argument("-q", "--quiet", action="store_true", help="No periodic status lines")
    ap.add_argument("--api-host", default=DEFAULT_API_HOST, help="REST bind address")
    ap.add_argument("--api-port", type=int, default=DEFAULT_API_PORT, help="REST bind port")
    ap.add_argument("--no-api", action="store_true", help="Do not start REST API")
    ap.add_argument(
        "--resubscribe-stale",
        action="store_true",
        help="Re-subscribe Slice SM when indications stall (can crash nearRT-RIC; default off)",
    )
    ap.add_argument("--docker", action="store_true", help="Force run in oai-flexric image")
    ap.add_argument("--host", action="store_true", help="Require local xapp_sdk")
    ap.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    ap.add_argument("--docker-net", default=DEFAULT_DOCKER_NET)
    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"WARN: ignoring unknown args: {unknown}", file=sys.stderr)

    if not args.resubscribe_stale and os.environ.get("NWS_XAPP_RESUBSCRIBE_STALE") == "1":
        args.resubscribe_stale = True

    conf = args.conf.expanduser().resolve()

    api_port, explicit = resolve_api_port_from_argv(sys.argv[1:])
    if not explicit and not args.no_api:
        available = find_available_api_port(args.api_port, args.api_host)
        if available != args.api_port:
            print(f"Port {args.api_port} in use — REST API will use {available}")
            args.api_port = available

    if not IN_DOCKER and not args.host and (args.docker or not can_import_sdk()):
        return reexec_via_docker(
            sys.argv[1:],
            conf=conf,
            image=args.docker_image,
            network=args.docker_net,
        )

    if not conf.is_file():
        print(f"ERROR: FlexRIC conf not found: {conf}", file=sys.stderr)
        return 1

    ric_container = os.environ.get("NWS_NEAR_RIC_CONTAINER", "nws-nearRT-RIC")
    warn_ric_stack_conflicts(ric_container)

    out: Optional[Path] = None
    if str(args.out).strip() and str(args.out) not in ("-", "/dev/null"):
        out = args.out.expanduser().resolve()
    ns_out: Optional[Path] = None
    if str(args.ns_out).strip() and str(args.ns_out) not in ("-", "/dev/null"):
        ns_out = args.ns_out.expanduser().resolve()

    # Bootstrap /docs in a forked process: FlexRIC init can hold the GIL and
    # starve a Python HTTP thread (listen works, accept hangs).
    boot_proc: Any = None
    httpd: Optional[ThreadingHTTPServer] = None
    if not args.no_api:
        boot_proc = start_bootstrap_api_process(args.api_host, args.api_port)

    def _stop_bootstrap() -> None:
        nonlocal boot_proc
        if boot_proc is None:
            return
        if boot_proc.is_alive():
            boot_proc.terminate()
            boot_proc.join(timeout=2.0)
        boot_proc = None

    ric = import_ric()
    print(f"Using conf: {conf}")
    ric_container = os.environ.get("NWS_NEAR_RIC_CONTAINER", "nws-nearRT-RIC")
    if os.environ.get("NWS_XAPP_RIC_E2_READY") != "1":
        if not wait_ric_e2_before_xapp_init(timeout_s=args.wait_e2, ric_container=ric_container):
            print(
                "ERROR: no gNB E2 on nearRT-RIC — xApp init would crash the RIC.\n"
                "  1. Enable e2_agent.near_ric_ip_addr in gNB YAML "
                f"({parse_near_ric_ip(conf)})\n"
                f"  2. docker restart {ric_container} nws-oai-gnb\n"
                "  3. Re-run xApp after RIC logs show 'E2 SETUP-REQUEST rx'",
                file=sys.stderr,
            )
            _stop_bootstrap()
            return 1
    elif IN_DOCKER:
        print("gNB E2 pre-checked on host (NWS_XAPP_RIC_E2_READY=1)", flush=True)
    if hasattr(ric, "init_conf"):
        ric.init_conf(str(conf))
    else:
        ric.init()
        print("WARN: xapp_sdk has no init_conf(); used init()", file=sys.stderr)

    print(f"Waiting up to {args.wait_e2:.0f}s for E2 nodes (nearRT-RIC)...")
    conn = wait_e2_nodes(ric, args.wait_e2)
    if not conn:
        print("ERROR: no E2 nodes connected to nearRT-RIC", file=sys.stderr)
        _stop_bootstrap()
        return 1

    if args.node_idx < 0 or args.node_idx >= len(conn):
        print(f"ERROR: --node-idx {args.node_idx} out of range (have {len(conn)} node(s))", file=sys.stderr)
        _stop_bootstrap()
        return 1

    for i, n in enumerate(conn):
        try:
            mcc = n.id.plmn.mcc
            mnc = n.id.plmn.mnc
            print(f"  E2[{i}]: PLMN {mcc:03d}/{mnc:02d}")
        except Exception:
            print(f"  E2[{i}]: (connected)")

    state = MonitorState(
        out=out,
        ns_out=ns_out,
        do_print=args.do_print or args.print_flexric,
        print_flexric=args.print_flexric,
        quiet=args.quiet,
    )
    cb = make_callback(ric, state)
    inter = interval_const(ric, str(args.interval))
    node = conn[args.node_idx]
    print(
        f"Subscribing Slice SM on E2[{args.node_idx}] every {args.interval} ms "
        f"(OAI NS policy via ind.ns_policy)",
        flush=True,
    )
    hndlr = ric.report_slice_sm(node.id, inter, cb)

    controller = SliceController(ric, node.id, state)
    if not args.no_api:
        _stop_bootstrap()
        httpd = start_api_server(args.api_host, args.api_port, controller)

    stop = {"flag": False, "sig_count": 0}

    def _on_sig(signum: int, _frame: Any) -> None:
        stop["sig_count"] += 1
        stop["flag"] = True
        if stop["sig_count"] == 1:
            print("\nShutting down (Ctrl-C again to force quit)...", flush=True)
        elif stop["sig_count"] >= 2:
            print("\nForce exit.", flush=True)
            os._exit(128 + (signum if signum < 128 else 0))

    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    last_resub_warn = 0.0
    resub_cooldown_sec = 30.0

    try:
        if args.duration > 0:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline and not stop["flag"]:
                time.sleep(0.2)
        else:
            print("Running until Ctrl-C (press again to force quit)...", flush=True)
            while not stop["flag"]:
                time.sleep(0.5)
                if not args.resubscribe_stale or stop["flag"]:
                    continue
                # Recover stalled Slice SM indication subscription. CONTROL can
                # still work while GET would otherwise freeze on the last ind.
                # SUBSCRIPTION_DELETE can assert/crash nearRT-RIC — opt-in only.
                age = state.indication_age_sec()
                now = time.monotonic()
                if (
                    age is not None
                    and age >= SliceController.INDICATION_STALE_SEC
                    and now - last_resub_warn >= resub_cooldown_sec
                ):
                    last_resub_warn = now
                    print(
                        f"WARN: Slice SM indications stale for {age:.0f}s "
                        f"(count={state.count}); re-subscribing",
                        flush=True,
                    )
                    if not run_with_timeout(
                        lambda: ric.rm_report_slice_sm(hndlr),
                        3.0,
                        "rm_report_slice_sm (re-subscribe)",
                    ):
                        continue

                    def _resub() -> None:
                        nonlocal hndlr
                        hndlr = ric.report_slice_sm(node.id, inter, cb)

                    if run_with_timeout(_resub, 5.0, "report_slice_sm (re-subscribe)"):
                        print("Re-subscribed Slice SM report", flush=True)
    finally:
        print(f"Stopping (indications received: {state.count})", flush=True)
        _stop_bootstrap()
        if httpd is not None:
            httpd.shutdown()
        run_with_timeout(lambda: ric.rm_report_slice_sm(hndlr), 3.0, "rm_report_slice_sm (shutdown)")
        try:
            n = 0
            while getattr(ric, "try_stop", 1) == 0 and n < 20 and not stop["flag"]:
                time.sleep(0.1)
                n += 1
        except Exception:
            pass

    if state.last is not None:
        if ns_out is not None:
            print(f"Last NS policy written to {ns_out}")
        if out is not None:
            print(f"Last full indication written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Monitor FlexRIC Slice SM xApp — OAI NS PRB policy (ind.ns_policy).

Connects to nearRT-RIC, subscribes to slice indications, and prints/writes the
actual network-slice PRB ratios (SST/SD, ul/dl, dedicated/min/max %). Does not
send slice control.

The indication also carries a FlexRIC STATIC/NVS/EDF demo model under
`flexric`; ignore that for NS lab work.

On the host there is usually no xapp_sdk (image is Python 3.12). By default this
script re-runs itself in oai-flexric:latest on network nws-oai-rf-sim.

Examples:
  python3 xapp_slice_monitor.py
  python3 xapp_slice_monitor.py --print
  cat out/rt_ns_slice_policy.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
# scripts/xapp -> nws
NWS_DIR = SCRIPT_DIR.parent.parent if (SCRIPT_DIR.parent.name == "scripts") else SCRIPT_DIR.parent
DEFAULT_CONF = Path(
    os.environ.get(
        "FLEXRIC_CONF",
        str(
            Path("/etc/flexric/flexric.conf")
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


def reexec_via_docker(argv: list[str], *, conf: Path, image: str, network: str) -> int:
    if not shutil.which("docker"):
        print("ERROR: docker not found and host has no xapp_sdk", file=sys.stderr)
        return 1
    script = Path(__file__).resolve()
    conf = conf.resolve()
    if not conf.is_file():
        print(f"ERROR: FlexRIC conf not found: {conf}", file=sys.stderr)
        return 1

    # Forward user args except launcher flags; force conf path inside container.
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
        if a in ("--conf", "--out") and i + 1 < len(argv):
            i += 2
            continue
        if a.startswith("--conf=") or a.startswith("--out="):
            i += 1
            continue
        forwarded.append(a)
        i += 1

    out_host = SCRIPT_DIR / "rt_slice_stats.json"
    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "-e",
        "NWS_XAPP_IN_DOCKER=1",
        "-e",
        "PYTHONPATH=/usr/local/flexric/xApp/python3",
        "-e",
        "LD_LIBRARY_PATH=/usr/local/lib",
        "-e",
        "FLEXRIC_CONF=/xapp/flexric.conf",
        "-v",
        f"{script}:/xapp/xapp_slice_monitor.py:ro",
        "-v",
        f"{conf}:/xapp/flexric.conf:ro",
        "-v",
        f"{out_host.parent}:/xapp/out",
        "-w",
        "/xapp",
        image,
        "python3",
        "/xapp/xapp_slice_monitor.py",
        "--conf",
        "/xapp/flexric.conf",
        "--out",
        "/xapp/out/rt_slice_stats.json",
        *forwarded,
    ]
    if sys.stdin.isatty() and sys.stdout.isatty():
        docker_argv.insert(3, "-it")

    print(f"No host xapp_sdk — running in {image} on network {network}")
    print("  (host Python is typically 3.10; image SDK needs 3.12)")
    return subprocess.call(docker_argv)


def slice_algo_name(type_id: int) -> str:
    return {1: "STATIC", 2: "NVS", 4: "EDF"}.get(type_id, f"unknown({type_id})")


def _fmt_sd(sd: Any) -> str:
    try:
        return f"0x{int(sd):06x}"
    except Exception:
        return str(sd)


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
                "direction": str(e.direction),
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
        "ns_policy": ns,  # actual OAI NS PRB ratios (SST/SD, ul/dl, ded/min/max %)
        "slices": ns,  # alias matching README-ns-slice-e2.json shape
        "flexric": flex,  # STATIC/NVS/EDF demo — ignore for NS lab work
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
        self._last_ns_key: Optional[str] = None

    def on_ind(self, ind: Any) -> None:
        data = slice_ind_to_dict(ind)
        self.count += 1
        self.last = data
        ns = data.get("ns_policy") or []
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
            elif changed or self.count == 1:
                print(json.dumps(ns_view, indent=2), flush=True)
        elif not self.quiet and (changed or self.count % 100 == 1):
            n_ns = len(ns)
            n_u = data.get("flexric", {}).get("UE", {}).get("num_of_ues", 0)
            summary = ", ".join(
                f"{e['direction']}:{e['sd']} ded={e['dedicated']:.0f}/"
                f"min={e['min']:.0f}/max={e['max']:.0f}"
                for e in ns[:8]
            )
            more = f" (+{n_ns - 8} more)" if n_ns > 8 else ""
            print(
                f"[ind #{self.count}] ns_slices={n_ns} ues={n_u}"
                + (f" | {summary}{more}" if summary else " | (empty ns_policy)"),
                flush=True,
            )


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


def available_intervals(ric: Any) -> list[str]:
    """ms values for which Interval_ms_<ms> exists in this SDK build."""
    found: list[str] = []
    for ms in INTERVAL_CHOICES:
        if hasattr(ric, f"Interval_ms_{ms}"):
            found.append(ms)
    # Also pick up any other Interval_ms_* the build may expose.
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Monitor OAI NS slice policy over FlexRIC Slice SM (E2 RAN func 145)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--conf",
        type=Path,
        default=Path(os.environ.get("FLEXRIC_CONF", DEFAULT_CONF)),
        help="FlexRIC/xApp conf (NEAR_RIC_IP); default $FLEXRIC_CONF or configs/flexric/flexric.conf",
    )
    ap.add_argument("--node-idx", type=int, default=0, help="E2 node index in conn_e2_nodes()")
    ap.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help="Report period in ms (Interval_ms_*); this FlexRIC build typically has 1,2,5,10",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Seconds to monitor (0 = until Ctrl-C)",
    )
    ap.add_argument(
        "--wait-e2",
        type=float,
        default=60.0,
        help="Seconds to wait for at least one E2 node",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Write latest full indication JSON (ns_policy + flexric demo)",
    )
    ap.add_argument(
        "--ns-out",
        type=Path,
        default=DEFAULT_NS_OUT,
        help="Write latest OAI NS policy JSON only (empty string to disable)",
    )
    ap.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Print NS policy JSON when it changes (use --print-flexric for full dump)",
    )
    ap.add_argument(
        "--print-flexric",
        action="store_true",
        help="With --print, dump full indication including FlexRIC STATIC/NVS/EDF demo",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="No periodic status lines")
    ap.add_argument(
        "--docker",
        action="store_true",
        help="Force run inside oai-flexric:latest on nws-oai-rf-sim",
    )
    ap.add_argument(
        "--host",
        action="store_true",
        help="Require local xapp_sdk (do not auto-use docker)",
    )
    ap.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE, help="FlexRIC image for --docker")
    ap.add_argument("--docker-net", default=DEFAULT_DOCKER_NET, help="Docker network with nearRT-RIC")
    args, unknown = ap.parse_known_args()
    # Keep compatibility if extra args sneak in
    if unknown:
        print(f"WARN: ignoring unknown args: {unknown}", file=sys.stderr)

    conf = args.conf.expanduser().resolve()

    # Auto docker when SDK missing on host (unless already in container or --host).
    if not IN_DOCKER and not args.host and (args.docker or not can_import_sdk()):
        return reexec_via_docker(
            sys.argv[1:],
            conf=conf,
            image=args.docker_image,
            network=args.docker_net,
        )

    if not conf.is_file():
        print(f"ERROR: FlexRIC conf not found: {conf}", file=sys.stderr)
        print("  source nws/configs/flexric/flexric.connection.env", file=sys.stderr)
        return 1

    out: Optional[Path] = None
    if str(args.out).strip() and str(args.out) not in ("-", "/dev/null"):
        out = args.out.expanduser().resolve()
    ns_out: Optional[Path] = None
    if str(args.ns_out).strip() and str(args.ns_out) not in ("-", "/dev/null"):
        ns_out = args.ns_out.expanduser().resolve()

    ric = import_ric()
    print(f"Using conf: {conf}")
    if hasattr(ric, "init_conf"):
        ric.init_conf(str(conf))
    else:
        ric.init()
        print("WARN: xapp_sdk has no init_conf(); used init() — ensure NEAR_RIC_IP matches", file=sys.stderr)

    print(f"Waiting up to {args.wait_e2:.0f}s for E2 nodes (nearRT-RIC)...")
    conn = wait_e2_nodes(ric, args.wait_e2)
    if not conn:
        print("ERROR: no E2 nodes connected to nearRT-RIC", file=sys.stderr)
        print("  Start RIC, e.g.:", file=sys.stderr)
        print(
            "    cd nws/docker-compose && docker compose -f docker-compose.open5gs.5slices.nsul.yaml up -d nws-nearRT-RIC",
            file=sys.stderr,
        )
        print("  Ensure gNB E2 is enabled and near_ric_ip_addr matches flexric.conf", file=sys.stderr)
        return 1

    if args.node_idx < 0 or args.node_idx >= len(conn):
        print(f"ERROR: --node-idx {args.node_idx} out of range (have {len(conn)} node(s))", file=sys.stderr)
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

    stop = {"flag": False}

    def _on_sig(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    try:
        if args.duration > 0:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline and not stop["flag"]:
                time.sleep(0.2)
        else:
            print("Monitoring NS slices until Ctrl-C...", flush=True)
            while not stop["flag"]:
                time.sleep(0.5)
    finally:
        print(f"Stopping (indications received: {state.count})")
        try:
            ric.rm_report_slice_sm(hndlr)
        except Exception as e:
            print(f"WARN: rm_report_slice_sm: {e}", file=sys.stderr)
        # Drain FlexRIC stop
        try:
            n = 0
            while getattr(ric, "try_stop", 1) == 0 and n < 20:
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

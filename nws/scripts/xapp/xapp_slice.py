#!/usr/bin/env python3
"""
FlexRIC Slice SM xApp — monitor OAI NS PRB policy + REST API to change it.

Subscribes to E2 Slice SM (RAN func 145) indications (`ind.ns_policy`) and
exposes a small HTTP API to GET / SET NS dedicated/min/max ratios via
`control_ns_slice_policy`.

Examples:
  python3 xapp_slice.py --print --api-port 8080
  curl -s http://192.168.201.143:8080/api/v1/slices | jq .
  curl -s -X PUT http://192.168.201.143:8080/api/v1/slices \\
    -H 'Content-Type: application/json' \\
    -d '{"slices":[{"sst":1,"sd":"0x000002","direction":"ul","dedicated":10,"min":10,"max":100}]}'
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

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
DEFAULT_API_HOST = os.environ.get("NWS_XAPP_API_HOST", "0.0.0.0")
DEFAULT_API_PORT = int(os.environ.get("NWS_XAPP_API_PORT", "8080"))
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
    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "-p",
        f"{DEFAULT_API_PORT}:{DEFAULT_API_PORT}",
        "-e",
        "NWS_XAPP_IN_DOCKER=1",
        "-e",
        "PYTHONPATH=/usr/local/flexric/xApp/python3",
        "-e",
        "LD_LIBRARY_PATH=/usr/local/lib",
        "-e",
        "FLEXRIC_CONF=/xapp/flexric.conf",
        "-v",
        f"{script}:/xapp/xapp_slice.py:ro",
        "-v",
        f"{conf}:/xapp/flexric.conf:ro",
        "-v",
        f"{out_host}:/xapp/out",
        "-w",
        "/xapp",
        image,
        "python3",
        "/xapp/xapp_slice.py",
        "--conf",
        "/xapp/flexric.conf",
        "--out",
        "/xapp/out/rt_slice_stats.json",
        "--ns-out",
        "/xapp/out/rt_ns_slice_policy.json",
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


def parse_sd(value: Any) -> int:
    if isinstance(value, int):
        return value
    s = str(value).strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s, 10)


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
                "direction": str(e.direction).lower(),
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
        self._last_ns_key: Optional[str] = None
        self.lock = threading.Lock()

    def on_ind(self, ind: Any) -> None:
        data = slice_ind_to_dict(ind)
        with self.lock:
            self.count += 1
            self.last = data
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

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            if self.last is None:
                return {"tstamp": None, "slices": [], "indications": self.count}
            ns = list(self.last.get("ns_policy") or [])
            return {
                "tstamp": self.last.get("tstamp"),
                "slices": ns,
                "indications": self.count,
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

    for d, total in sum_ded.items():
        if total > 100.0 + 1e-6:
            errors.append(f"sum(dedicated) for {d} is {total:.1f}% (> 100%)")
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
    def __init__(self, ric: Any, node_id: Any, state: MonitorState) -> None:
        self.ric = ric
        self.node_id = node_id
        self.state = state
        self.lock = threading.Lock()
        self.last_set: Optional[dict[str, Any]] = None

    def get_slices(self) -> dict[str, Any]:
        return self.state.snapshot()

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
            e.direction = n["direction"]
            e.dedicated_pct = n["dedicated"]
            e.min_pct = n["min"]
            e.max_pct = n["max"]
            swig_vec.push_back(e)

        with self.lock:
            self.ric.control_ns_slice_policy(self.node_id, swig_vec)
            self.last_set = {
                "ok": True,
                "sent": [
                    {
                        "sst": n["sst"],
                        "sd": n["sd"],
                        "direction": n["direction"],
                        "dedicated": n["dedicated"],
                        "min": n["min"],
                        "max": n["max"],
                    }
                    for n in normalized
                ],
                "note": "E2 CONTROL ACK means RIC got a reply; confirm via GET /api/v1/slices or gNB log 'NS E2 SET applied'",
            }
            return dict(self.last_set)

    def patch_slice(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Merge one entry into the current indication policy, then SET the merged list (excluding 0xffffff)."""
        patch = normalize_entry(raw)
        if patch["sd_int"] == NS_DEFAULT_SD:
            raise ValueError("sd=0xffffff (default slice) cannot be SET over E2")

        snap = self.state.snapshot()
        current = [normalize_entry(e) for e in (snap.get("slices") or [])]
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

        # set_slices expects raw-ish dicts with sd as hex/int
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
            "sum(dedicated) ≤ 100% per direction, "
            "`sd=0xffffff` cannot be SET.\n\n"
            "E2 CONTROL ACK only means the RIC got a reply — confirm with "
            "GET `/api/v1/slices` or gNB log `NS E2 SET applied`."
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
        if path == "/openapi.json":
            self._send(200, OPENAPI_SPEC)
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
                    "slices": len(snap.get("slices") or []),
                    "docs": "/docs",
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
                    "GET /openapi.json": "OpenAPI 3 JSON",
                    "GET /api/v1/slices": "current NS policy from last indication",
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


def _print_api_urls(host: str, port: int) -> None:
    print(f"REST API listening on http://{host}:{port}", flush=True)
    print(f"  Swagger UI  http://{host}:{port}/docs", flush=True)
    print(f"  OpenAPI     http://{host}:{port}/openapi.json", flush=True)
    if host in ("0.0.0.0", "::", ""):
        print(f"  (lab)       http://10.1.132.200:{port}/docs", flush=True)
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
    ap.add_argument("--docker", action="store_true", help="Force run in oai-flexric image")
    ap.add_argument("--host", action="store_true", help="Require local xapp_sdk")
    ap.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    ap.add_argument("--docker-net", default=DEFAULT_DOCKER_NET)
    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"WARN: ignoring unknown args: {unknown}", file=sys.stderr)

    conf = args.conf.expanduser().resolve()

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
            print("Running until Ctrl-C...", flush=True)
            while not stop["flag"]:
                time.sleep(0.5)
    finally:
        print(f"Stopping (indications received: {state.count})")
        _stop_bootstrap()
        if httpd is not None:
            httpd.shutdown()
        try:
            ric.rm_report_slice_sm(hndlr)
        except Exception as e:
            print(f"WARN: rm_report_slice_sm: {e}", file=sys.stderr)
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

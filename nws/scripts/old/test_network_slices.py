#!/usr/bin/env python3
"""
Offline checks for OAI gNB network-slicing (SCHE_NS) configuration and logs.

Mirrors constraints enforced in openair2/GNB_APP/gnb_config.c:set_slice_config().

Requires: PyYAML (pip install pyyaml)

Examples:
  python3 test_network_slices.py check-yaml ../configs/gnb/gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsul.yaml
  python3 test_network_slices.py check-log /tmp/oai_gnb.log
  python3 test_network_slices.py check-yaml gnb.yaml --log /tmp/oai_gnb.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


def _need_yaml():
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("Install PyYAML: pip install pyyaml", file=sys.stderr)
        sys.exit(2)


def load_yaml(path: str) -> dict[str, Any]:
    _need_yaml()
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root, got {type(data).__name__}")
    return data


def _as_float(x: Any, default: float = -1.0) -> float:
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    raise TypeError(f"Expected number, got {type(x).__name__}")


def _parse_sd(raw: Any) -> int:
    """Normalize SST/SD from YAML (int or hex string)."""
    if isinstance(raw, int):
        return raw & 0xFFFFFF
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s.startswith("0x"):
            return int(s, 16) & 0xFFFFFF
        return int(s, 16) & 0xFFFFFF if all(c in "0123456789abcdef" for c in s) else int(s) & 0xFFFFFF
    raise TypeError(f"Bad sd type: {type(raw).__name__}")


@dataclass
class SliceRow:
    slice_id: int
    sst: int
    sd: int
    dedicated: float
    min_p: float
    max_p: float
    dl_dedicated: float = -1.0
    dl_min: float = -1.0
    dl_max: float = -1.0
    ul_dedicated: float = -1.0
    ul_min: float = -1.0
    ul_max: float = -1.0


def _slice_rows(slices: Iterable[dict[str, Any]]) -> list[SliceRow]:
    out: list[SliceRow] = []
    for raw in slices:
        row = SliceRow(
            slice_id=int(raw["slice_id"]),
            sst=int(raw["sst"]),
            sd=_parse_sd(raw["sd"]),
            dedicated=float(raw["dedicated_prb_ratio"]),
            min_p=float(raw["min_prb_ratio"]),
            max_p=float(raw["max_prb_ratio"]),
            dl_dedicated=_as_float(raw.get("dl_dedicated_prb_ratio"), -1.0),
            dl_min=_as_float(raw.get("dl_min_prb_ratio"), -1.0),
            dl_max=_as_float(raw.get("dl_max_prb_ratio"), -1.0),
            ul_dedicated=_as_float(raw.get("ul_dedicated_prb_ratio"), -1.0),
            ul_min=_as_float(raw.get("ul_min_prb_ratio"), -1.0),
            ul_max=_as_float(raw.get("ul_max_prb_ratio"), -1.0),
        )
        out.append(row)
    return out


def _effective_dl_ul(row: SliceRow) -> tuple[float, float, float, float, float, float]:
    """Returns (dl_ded%, dl_min%, dl_max%, ul_ded%, ul_min%, ul_max%) as percentages."""
    d, mn, mx = row.dedicated, row.min_p, row.max_p
    if row.dl_dedicated < 0.0:
        dl_d, dl_mn, dl_mx = d, mn, mx
    else:
        dl_d = row.dl_dedicated
        dl_mn = mn if row.dl_min < 0.0 else row.dl_min
        dl_mx = mx if row.dl_max < 0.0 else row.dl_max
    if row.ul_dedicated < 0.0:
        ul_d, ul_mn, ul_mx = d, mn, mx
    else:
        ul_d = row.ul_dedicated
        ul_mn = mn if row.ul_min < 0.0 else row.ul_min
        ul_mx = mx if row.ul_max < 0.0 else row.ul_max
    return (dl_d, dl_mn, dl_mx, ul_d, ul_mn, ul_mx)


def validate_slices_yaml(data: dict[str, Any]) -> list[str]:
    """Return list of error strings; empty if OK."""
    errors: list[str] = []
    slices = data.get("Slices")
    if slices is None:
        errors.append("No top-level 'Slices' list (nothing to validate for SCHE_NS).")
        return errors
    if not isinstance(slices, list) or not slices:
        errors.append("'Slices' must be a non-empty list.")
        return errors

    try:
        rows = _slice_rows(slices)
    except (KeyError, TypeError, ValueError) as e:
        return [f"Slice parse error: {e}"]

    sched = None
    macrlcs = data.get("MACRLCs")
    if isinstance(macrlcs, list):
        for m in macrlcs:
            if isinstance(m, dict) and "scheduler_type" in m:
                sched = int(m["scheduler_type"])
                break
    if sched is not None and sched != 1:
        errors.append(
            f"MACRLCs scheduler_type is {sched} (1=SCHE_NS). Slices block is only meaningful for SCHE_NS."
        )

    seen_ids: set[int] = set()
    seen_nssai: set[tuple[int, int]] = set()
    sum_ded = sum_dl = sum_ul = 0.0

    for i, row in enumerate(rows):
        sid = row.slice_id
        if sid in seen_ids:
            errors.append(f"Duplicate slice_id {sid}.")
        seen_ids.add(sid)
        if not (0 <= sid <= 1023):
            errors.append(f"Slice {i}: slice_id must be in [0, 1023], got {sid}.")
        if not (0 <= row.sst <= 255):
            errors.append(f"Slice {i}: SST must be in [0, 255].")
        if row.sd > 0xFFFFFF:
            errors.append(f"Slice {i}: SD must be <= 0xffffff.")

        for name, v in (
            ("dedicated_prb_ratio", row.dedicated),
            ("min_prb_ratio", row.min_p),
            ("max_prb_ratio", row.max_p),
        ):
            if not (0.0 <= v <= 100.0):
                errors.append(f"Slice {i}: {name} must be in [0, 100], got {v}.")

        if row.dedicated > row.min_p:
            errors.append(
                f"Slice {sid}: dedicated ({row.dedicated}%) must be <= min ({row.min_p}%)."
            )
        if row.min_p > row.max_p:
            errors.append(f"Slice {sid}: min ({row.min_p}%) must be <= max ({row.max_p}%).")

        dl_d, dl_mn, dl_mx, ul_d, ul_mn, ul_mx = _effective_dl_ul(row)
        nssai = (row.sst, row.sd)
        if nssai in seen_nssai:
            errors.append(f"Duplicate NSSAI SST={row.sst}, SD=0x{row.sd:06x}.")
        seen_nssai.add(nssai)

        if row.dl_dedicated >= 0.0:
            if not (0.0 <= row.dl_dedicated <= 100.0):
                errors.append(f"Slice {sid}: dl_dedicated_prb_ratio invalid.")
            if dl_d > dl_mn:
                errors.append(f"Slice {sid}: DL dedicated must be <= DL min.")
            if dl_mn > dl_mx:
                errors.append(f"Slice {sid}: DL min must be <= DL max.")
        if row.ul_dedicated >= 0.0:
            if not (0.0 <= row.ul_dedicated <= 100.0):
                errors.append(f"Slice {sid}: ul_dedicated_prb_ratio invalid.")
            if ul_d > ul_mn:
                errors.append(f"Slice {sid}: UL dedicated must be <= UL min.")
            if ul_mn > ul_mx:
                errors.append(f"Slice {sid}: UL min must be <= UL max.")

        sum_ded += row.dedicated
        sum_dl += dl_d
        sum_ul += ul_d

    if sum_ded > 100.0 + 1e-6:
        errors.append(f"Sum of dedicated_prb_ratio is {sum_ded:.1f}% (> 100%).")
    if sum_dl > 100.0 + 1e-6:
        errors.append(f"Sum of effective DL dedicated is {sum_dl:.1f}% (> 100%).")
    if sum_ul > 100.0 + 1e-6:
        errors.append(f"Sum of effective UL dedicated is {sum_ul:.1f}% (> 100%).")

    return errors


def nssai_from_gnb(data: dict[str, Any]) -> list[tuple[int, int]]:
    """Collect (sst, sd) from first gNB plmn snssaiList."""
    gnbs = data.get("gNBs")
    if not isinstance(gnbs, list) or not gnbs:
        return []
    plmn_list = gnbs[0].get("plmn_list") if isinstance(gnbs[0], dict) else None
    if not isinstance(plmn_list, list) or not plmn_list:
        return []
    snssai = plmn_list[0].get("snssaiList")
    if not isinstance(snssai, list):
        return []
    out: list[tuple[int, int]] = []
    for s in snssai:
        if isinstance(s, dict) and "sst" in s:
            out.append((int(s["sst"]), _parse_sd(s.get("sd", 0))))
    return out


def warn_slices_vs_snssai(data: dict[str, Any]) -> list[str]:
    """Non-fatal: Slices entries should appear in broadcast NSSAI."""
    warns: list[str] = []
    slices = data.get("Slices")
    if not isinstance(slices, list):
        return warns
    slice_nssai = {_parse_sd(s["sd"]) for s in slices if isinstance(s, dict) and "sd" in s}
    cell_nssai = {sd for _, sd in nssai_from_gnb(data)}
    missing = slice_nssai - cell_nssai
    if missing:
        warns.append(
            "Slice SD(s) not found in gNB plmn snssaiList: "
            + ", ".join(f"0x{m:06x}" for m in sorted(missing))
        )
    return warns


# Log patterns (see LOG_I in gnb_config.c)
_RE_SCHED = re.compile(r"Scheduler type set to (\d+) from configuration")
_RE_SLICE = re.compile(
    r"Configured slice (\d+): SST=(\d+), SD=0x([0-9a-fA-F]+), "
    r"shared Dedicated=([\d.]+)%, Min=([\d.]+)%, Max=([\d.]+)% \| "
    r"DL: ([\d.]+)%, ([\d.]+)%, ([\d.]+)% \| UL: ([\d.]+)%, ([\d.]+)%, ([\d.]+)%"
)
_RE_TOTAL = re.compile(
    r"Configured (\d+) network slices \(DL total dedicated: ([\d.]+)%, UL total dedicated: ([\d.]+)%\)"
)


@dataclass
class LogSliceReport:
    scheduler_type: Optional[int] = None
    slices: list[dict[str, Any]] = field(default_factory=list)
    total_count: Optional[int] = None
    dl_total: Optional[float] = None
    ul_total: Optional[float] = None


def parse_gnb_log(text: str) -> LogSliceReport:
    rep = LogSliceReport()
    for line in text.splitlines():
        m = _RE_SCHED.search(line)
        if m:
            rep.scheduler_type = int(m.group(1))
        m = _RE_SLICE.search(line)
        if m:
            rep.slices.append(
                {
                    "slice_id": int(m.group(1)),
                    "sst": int(m.group(2)),
                    "sd": int(m.group(3), 16),
                    "shared_dedicated": float(m.group(4)),
                    "shared_min": float(m.group(5)),
                    "shared_max": float(m.group(6)),
                    "dl_dedicated": float(m.group(7)),
                    "dl_min": float(m.group(8)),
                    "dl_max": float(m.group(9)),
                    "ul_dedicated": float(m.group(10)),
                    "ul_min": float(m.group(11)),
                    "ul_max": float(m.group(12)),
                }
            )
        m = _RE_TOTAL.search(line)
        if m:
            rep.total_count = int(m.group(1))
            rep.dl_total = float(m.group(2))
            rep.ul_total = float(m.group(3))
    return rep


def compare_yaml_log(data: dict[str, Any], rep: LogSliceReport) -> list[str]:
    """Compare YAML Slices to log summary when both present."""
    issues: list[str] = []
    slices = data.get("Slices")
    if not isinstance(slices, list):
        return ["No YAML Slices to compare."]
    n_yaml = len(slices)
    if rep.total_count is not None and rep.total_count != n_yaml:
        issues.append(f"Slice count: YAML has {n_yaml}, log reports {rep.total_count}.")
    rows = _slice_rows(slices)
    sum_dl = sum(_effective_dl_ul(r)[0] for r in rows)
    sum_ul = sum(_effective_dl_ul(r)[3] for r in rows)
    if rep.dl_total is not None and abs(rep.dl_total - sum_dl) > 0.15:
        issues.append(f"DL total dedicated: YAML effective sum {sum_dl:.2f}%, log {rep.dl_total:.2f}%.")
    if rep.ul_total is not None and abs(rep.ul_total - sum_ul) > 0.15:
        issues.append(f"UL total dedicated: YAML effective sum {sum_ul:.2f}%, log {rep.ul_total:.2f}%.")
    return issues


def cmd_check_yaml(args: argparse.Namespace) -> int:
    data = load_yaml(args.yaml)
    errs = validate_slices_yaml(data)
    warns = warn_slices_vs_snssai(data)
    if args.json:
        print(
            json.dumps(
                {"ok": not errs, "errors": errs, "warnings": warns},
                indent=2,
            )
        )
        return 0 if not errs else 1
    for w in warns:
        print(f"Warning: {w}")
    if errs:
        print("Configuration errors:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"OK: {args.yaml} — {len(data.get('Slices', []))} slice(s) pass static checks.")
    return 0


def cmd_check_log(args: argparse.Namespace) -> int:
    with open(args.log, encoding="utf-8", errors="replace") as f:
        text = f.read()
    rep = parse_gnb_log(text)
    if args.json:
        print(
            json.dumps(
                {
                    "scheduler_type": rep.scheduler_type,
                    "slice_lines": len(rep.slices),
                    "total_count": rep.total_count,
                    "dl_total_pct": rep.dl_total,
                    "ul_total_pct": rep.ul_total,
                    "slices": rep.slices,
                },
                indent=2,
            )
        )
        return 0
    print(f"Log: {args.log}")
    if rep.scheduler_type is not None:
        print(f"  Scheduler type: {rep.scheduler_type} (1 = SCHE_NS)")
    print(f"  Parsed 'Configured slice' lines: {len(rep.slices)}")
    if rep.total_count is not None:
        print(
            f"  Summary line: {rep.total_count} slices, "
            f"DL total dedicated {rep.dl_total}%, UL total dedicated {rep.ul_total}%"
        )
    if not rep.slices and rep.total_count is None:
        print("  (No slice initialization lines found — wrong log or gNB exited before MAC init.)")
        return 1
    return 0


def cmd_check_both(args: argparse.Namespace) -> int:
    data = load_yaml(args.yaml)
    with open(args.log, encoding="utf-8", errors="replace") as f:
        rep = parse_gnb_log(f.read())
    errs = validate_slices_yaml(data)
    warns = warn_slices_vs_snssai(data)
    cmp_issues = compare_yaml_log(data, rep)

    if args.json:
        print(
            json.dumps(
                {
                    "yaml_errors": errs,
                    "warnings": warns,
                    "compare_issues": cmp_issues,
                    "log": {
                        "scheduler_type": rep.scheduler_type,
                        "slice_lines": len(rep.slices),
                        "total_count": rep.total_count,
                        "dl_total": rep.dl_total,
                        "ul_total": rep.ul_total,
                    },
                },
                indent=2,
            )
        )
        rc = 0 if not errs and not cmp_issues else 1
        return rc

    for w in warns:
        print(f"Warning: {w}")
    if errs:
        print("YAML errors:")
        for e in errs:
            print(f"  - {e}")
        return 1
    if cmp_issues:
        print("YAML vs log mismatch:")
        for e in cmp_issues:
            print(f"  - {e}")
        return 1
    print("OK: YAML checks passed and log matches slice summary.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Network slice (SCHE_NS) YAML + log checks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    y = sub.add_parser("check-yaml", help="Validate gNB YAML Slices block")
    y.add_argument("yaml", help="Path to gNB YAML")
    y.add_argument("--log", help="Also parse gNB log and compare totals")
    y.add_argument("--json", action="store_true", help="Machine-readable output")
    y.set_defaults(func=lambda a: cmd_check_both(a) if a.log else cmd_check_yaml(a))

    lg = sub.add_parser("check-log", help="Parse slice lines from gNB log")
    lg.add_argument("log", help="Path to gNB stdout/stderr log")
    lg.add_argument("--json", action="store_true")
    lg.set_defaults(func=cmd_check_log)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

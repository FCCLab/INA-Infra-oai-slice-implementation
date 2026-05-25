#!/usr/bin/env python3
"""
Five UE monitor for dongles 192.168.101.1 .. 192.168.105.1.

Terminal output refreshes every second with:
UE | Dongle Status | 5G Status
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
import ipaddress
import logging
from pathlib import Path
import re
import subprocess
import time


TARGET_5G_IP = "10.45.0.1"
DEFAULT_LOG_FILE = str(Path(__file__).resolve().with_name("five_ue_monitor.log"))
LOGGER = logging.getLogger("five_ue_monitor")


@dataclass(frozen=True)
class UEConfig:
    name: str
    dongle_ip: str
    lan_subnet: str


@dataclass
class UERow:
    ue: str
    dongle_status: str
    fiveg_status: str


def default_ues() -> list[UEConfig]:
    configs: list[UEConfig] = []
    for host in range(101, 106):
        ue_idx = host - 100
        configs.append(
            UEConfig(
                name=f"UE{ue_idx}",
                dongle_ip=f"192.168.{host}.1",
                lan_subnet=f"192.168.{host}.0/24",
            )
        )
    return configs


def run_command(cmd: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def ping_host(
    host: str,
    timeout_s: int = 1,
    iface: str | None = None,
    context: str = "",
) -> bool:
    cmd = ["ping", "-c", "1", "-W", str(timeout_s)]
    if iface:
        cmd.extend(["-I", iface])
    cmd.append(host)
    result = run_command(cmd, timeout=timeout_s + 1.5)
    if result is None:
        LOGGER.warning("PING_FAIL %s host=%s iface=%s reason=command_error", context, host, iface)
        return False

    ok = result.returncode == 0
    stdout_tail = (result.stdout or "").strip().splitlines()
    summary = stdout_tail[-1] if stdout_tail else "no_output"
    if ok:
        LOGGER.info("PING_OK %s host=%s iface=%s summary=%s", context, host, iface, summary)
    else:
        stderr_tail = (result.stderr or "").strip().replace("\n", " ")
        LOGGER.warning(
            "PING_FAIL %s host=%s iface=%s rc=%s summary=%s stderr=%s",
            context,
            host,
            iface,
            result.returncode,
            summary,
            stderr_tail,
        )
    return ok


def find_interface_via_route(target_ip: str) -> str | None:
    result = run_command(["ip", "-4", "route", "get", target_ip], timeout=2.0)
    if result is None or result.returncode != 0:
        return None
    output = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"\bdev\s+(\S+)", output)
    if not match:
        return None
    return match.group(1)


def find_interface_via_subnet(lan_subnet: str) -> str | None:
    network = ipaddress.ip_network(lan_subnet, strict=False)
    result = run_command(["ip", "-o", "-4", "addr", "show"], timeout=2.0)
    if result is None or result.returncode != 0:
        return None

    # Example line: "2: eth0    inet 192.168.101.20/24 brd ..."
    iface_re = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/\d+")
    for line in (result.stdout or "").splitlines():
        match = iface_re.match(line.strip())
        if not match:
            continue
        iface_name, ip_text = match.groups()
        try:
            addr = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if addr in network:
            return iface_name
    return None


def find_interface_for_ue(ue: UEConfig) -> str | None:
    iface = find_interface_via_route(ue.dongle_ip)
    if iface:
        LOGGER.info("IFACE_FOUND ue=%s method=route dongle_ip=%s iface=%s", ue.name, ue.dongle_ip, iface)
        return iface
    iface = find_interface_via_subnet(ue.lan_subnet)
    if iface:
        LOGGER.info(
            "IFACE_FOUND ue=%s method=subnet subnet=%s iface=%s", ue.name, ue.lan_subnet, iface
        )
    else:
        LOGGER.warning(
            "IFACE_NOT_FOUND ue=%s dongle_ip=%s subnet=%s", ue.name, ue.dongle_ip, ue.lan_subnet
        )
    return iface


def collect_rows(ues: list[UEConfig], target_5g_ip: str, ping_timeout_s: int) -> list[UERow]:
    dongle_results: dict[str, bool] = {}
    iface_map: dict[str, str | None] = {}
    fiveg_results: dict[str, bool | None] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(10, len(ues) * 2)) as pool:
        dongle_future_map = {
            ue.name: pool.submit(
                ping_host,
                ue.dongle_ip,
                ping_timeout_s,
                None,
                f"ue={ue.name} type=dongle",
            )
            for ue in ues
        }

        for ue in ues:
            iface_map[ue.name] = find_interface_for_ue(ue)

        fiveg_future_map: dict[str, concurrent.futures.Future[bool]] = {}
        for ue in ues:
            iface = iface_map[ue.name]
            if iface is None:
                fiveg_results[ue.name] = None
                continue
            fiveg_future_map[ue.name] = pool.submit(
                ping_host,
                target_5g_ip,
                ping_timeout_s,
                iface,
                f"ue={ue.name} type=5g",
            )

        for ue_name, future in dongle_future_map.items():
            dongle_results[ue_name] = future.result()

        for ue_name, future in fiveg_future_map.items():
            fiveg_results[ue_name] = future.result()

    rows: list[UERow] = []
    for ue in ues:
        dongle_status = "UP" if dongle_results.get(ue.name, False) else "DOWN"
        fiveg_value = fiveg_results.get(ue.name)
        if fiveg_value is None:
            fiveg_status = "NO_IFACE"
        else:
            fiveg_status = "UP" if fiveg_value else "DOWN"
        rows.append(UERow(ue=ue.name, dongle_status=dongle_status, fiveg_status=fiveg_status))
    return rows


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def render_table(rows: list[UERow], target_5g_ip: str, interval_s: float) -> None:
    clear_screen()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"Five UE Monitor  |  target={target_5g_ip}  |  refresh={interval_s:.1f}s")
    print(f"Updated: {now_str}")
    print()

    widths = (6, 16, 12)
    headers = ("UE", "Dongle Status", "5G Status")
    sep = f"+-{'-' * widths[0]}-+-{'-' * widths[1]}-+-{'-' * widths[2]}-+"
    print(sep)
    print(
        f"| {headers[0]:<{widths[0]}} | {headers[1]:<{widths[1]}} | {headers[2]:<{widths[2]}} |"
    )
    print(sep)
    for row in rows:
        print(
            f"| {row.ue:<{widths[0]}} | {row.dongle_status:<{widths[1]}} | {row.fiveg_status:<{widths[2]}} |"
        )
    print(sep)
    print()
    print("Press Ctrl+C to exit.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor 5 dongles and per-UE 5G path status.")
    parser.add_argument(
        "--target-5g-ip",
        default=TARGET_5G_IP,
        help=f"5G target IP to ping via each discovered interface (default: {TARGET_5G_IP})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--ping-timeout",
        type=int,
        default=1,
        help="Ping timeout in seconds (default: 1)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Number of refresh iterations (0 = run forever)",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILE,
        help=f"Path to log file (default: {DEFAULT_LOG_FILE})",
    )
    return parser.parse_args()


def configure_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
        force=True,
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_file)
    LOGGER.info("MONITOR_START target=%s interval=%s", args.target_5g_ip, args.interval)
    ues = default_ues()
    iteration = 0

    try:
        while True:
            started = time.monotonic()
            rows = collect_rows(ues, args.target_5g_ip, args.ping_timeout)
            render_table(rows, args.target_5g_ip, args.interval)
            LOGGER.info(
                "CYCLE_DONE %s",
                " ".join(f"{r.ue}:dongle={r.dongle_status},5g={r.fiveg_status}" for r in rows),
            )
            iteration += 1
            if args.iterations > 0 and iteration >= args.iterations:
                break

            elapsed = time.monotonic() - started
            sleep_time = max(0.0, args.interval - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nStopped.")
        LOGGER.info("MONITOR_STOP reason=keyboard_interrupt")
    LOGGER.info("MONITOR_STOP reason=normal_exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

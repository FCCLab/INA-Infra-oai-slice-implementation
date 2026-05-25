#!/usr/bin/env python3
"""
Configure APN profiles for five Pegatron dongles.

Default behavior:
- Targets 192.168.101.1 .. 192.168.105.1
- Deletes existing APN profiles, then creates APN "oai" with IPv4 (PdpType=0)
- Sets that profile as default when possible
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


LOGGER = logging.getLogger("five_ue_config")
DEFAULT_DONGLE_IPS = [f"192.168.{x}.1" for x in range(101, 106)]
DEFAULT_ENDPOINT = "/fibo/webapi"
DEFAULT_LOG_FILE = str(Path(__file__).resolve().with_name("five_ues_config.log"))
DEFAULT_CONFIG_FILE = Path(__file__).resolve().with_name("five_ues_config.yaml")


@dataclass
class ConfigResult:
    ip: str
    ok: bool
    message: str


@dataclass
class DongleTarget:
    ip: str
    endpoint: str
    password: str | None


class DongleClient:
    def __init__(self, ip: str, endpoint: str = "/fibo/webapi", password: str | None = None):
        self.ip = ip
        self.api_url = f"http://{ip}{endpoint}"
        self.password = password
        self.session = requests.Session()
        self.request_id = 0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": self._next_id()}
        if params:
            payload["params"] = params

        try:
            response = self.session.post(self.api_url, json=payload, timeout=8)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{self.ip}: API call failed for {method}: {exc}") from exc

        if "error" in data:
            raise RuntimeError(f"{self.ip}: API error for {method}: {data['error']}")
        return data.get("result")

    def login(self) -> None:
        if not self.password:
            return

        attempts = [
            {"password": self.password},
            {"Password": self.password},
            {"PassWord": self.password},
        ]
        last_error: RuntimeError | None = None
        for params in attempts:
            try:
                self.call("Login", params)
                return
            except RuntimeError as exc:
                last_error = exc
                # Some firmware rejects one shape but accepts another.
                continue
        if last_error is not None:
            raise last_error

    def get_profile_list(self) -> Any:
        return self.call("GetProfileList")

    def add_profile(
        self,
        name: str,
        apn: str,
        user: str = "",
        password: str = "",
        auth_type: int = 0,
        pdp_type: int = 2,
    ) -> Any:
        return self.call(
            "AddProfile",
            {
                "ProfileName": name,
                "APN": apn,
                "UserName": user,
                "Password": password,
                "AuthType": auth_type,
                "PdpType": pdp_type,
            },
        )

    def set_default_profile(self, profile_index: int) -> Any:
        return self.call("SetDefaultProfile", {"ProfileIndex": profile_index})

    def delete_profile(self, profile_index: int) -> Any:
        try:
            return self.call("DeleteProfile", {"ProfileIndex": profile_index})
        except RuntimeError as exc:
            raise RuntimeError(
                f"{self.ip}: DeleteProfile failed for index {profile_index}. "
                f"Firmware may not allow APN deletion. Detail: {exc}"
            ) from exc


def _iter_dicts(obj: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        out.append(obj)
        for value in obj.values():
            out.extend(_iter_dicts(value))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_iter_dicts(item))
    return out


def _extract_index(profile: dict[str, Any]) -> int | None:
    for key in ("ProfileIndex", "profileIndex", "Index", "index"):
        value = profile.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _extract_apn(profile: dict[str, Any]) -> str:
    for key in ("APN", "apn"):
        value = profile.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _extract_pdp_type(profile: dict[str, Any]) -> int | None:
    for key in ("PdpType", "pdpType", "IPType", "ipType"):
        value = profile.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def summarize_profiles(profile_list: Any) -> list[str]:
    summaries: list[str] = []
    for profile in _iter_dicts(profile_list):
        profile_index = _extract_index(profile)
        apn = _extract_apn(profile)
        pdp_type = _extract_pdp_type(profile)
        if profile_index is None and not apn:
            continue
        idx_text = str(profile_index) if profile_index is not None else "?"
        apn_text = apn if apn else "?"
        pdp_text = str(pdp_type) if pdp_type is not None else "?"
        summaries.append(f"idx={idx_text},apn={apn_text},pdp={pdp_text}")
    return summaries


def extract_profile_entries(profile_list: Any) -> list[tuple[int, str, int | None]]:
    entries: list[tuple[int, str, int | None]] = []
    for profile in _iter_dicts(profile_list):
        profile_index = _extract_index(profile)
        if profile_index is None:
            continue
        entries.append((profile_index, _extract_apn(profile), _extract_pdp_type(profile)))
    return entries


def find_matching_profile(profile_list: Any, apn: str, pdp_type: int) -> int | None:
    for profile in _iter_dicts(profile_list):
        current_apn = _extract_apn(profile)
        current_type = _extract_pdp_type(profile)
        profile_index = _extract_index(profile)
        if profile_index is None:
            continue
        if current_apn == apn and (current_type is None or current_type == pdp_type):
            return profile_index
    return None


def is_unauthorized_error(exc: RuntimeError) -> bool:
    text = str(exc)
    return "401" in text or "Unauthorized" in text


def get_profile_list_with_auth(client: DongleClient) -> Any:
    try:
        return client.get_profile_list()
    except RuntimeError as exc:
        if not client.password or not is_unauthorized_error(exc):
            raise
        LOGGER.info("[%s] profile list unauthorized, attempting Login then retry", client.ip)
        client.login()
        return client.get_profile_list()


def configure_one_dongle(
    ip: str,
    endpoint: str,
    password: str | None,
    apn: str,
    profile_name: str,
    pdp_type: int,
    set_default: bool,
) -> ConfigResult:
    try:
        client = DongleClient(ip=ip, endpoint=endpoint, password=password)
        profile_list_before = get_profile_list_with_auth(client)
        profile_summaries_before = summarize_profiles(profile_list_before)
        if profile_summaries_before:
            LOGGER.info("[%s] current APN profiles (before): %s", ip, " | ".join(profile_summaries_before))
        else:
            LOGGER.info("[%s] current APN profiles (before): none/unknown", ip)

        # Step 1: delete all profiles first.
        before_entries = extract_profile_entries(profile_list_before)
        deleted_indices: list[int] = []
        for profile_index, _, _ in sorted(before_entries, key=lambda x: x[0], reverse=True):
            client.delete_profile(profile_index)
            deleted_indices.append(profile_index)

        if deleted_indices:
            LOGGER.info("[%s] deleted profile indices: %s", ip, ",".join(str(i) for i in deleted_indices))
        else:
            LOGGER.info("[%s] no existing profile indices found to delete", ip)

        # Step 2: add only the target APN (oai/IPv4 by default).
        client.add_profile(
            name=profile_name,
            apn=apn,
            user="",
            password="",
            auth_type=0,
            pdp_type=pdp_type,
        )

        profile_list_after_add = get_profile_list_with_auth(client)
        profile_index = find_matching_profile(profile_list_after_add, apn=apn, pdp_type=pdp_type)
        if profile_index is None:
            return ConfigResult(
                ip=ip,
                ok=False,
                message=f"added profile but could not find APN={apn} PdpType={pdp_type} index",
            )

        if set_default:
            client.set_default_profile(profile_index)

        # Step 3: check final state.
        profile_list_after = get_profile_list_with_auth(client)
        profile_summaries_after = summarize_profiles(profile_list_after)
        if profile_summaries_after:
            LOGGER.info("[%s] current APN profiles (after): %s", ip, " | ".join(profile_summaries_after))
        else:
            LOGGER.info("[%s] current APN profiles (after): none/unknown", ip)

        verified_index = find_matching_profile(profile_list_after, apn=apn, pdp_type=pdp_type)
        if verified_index is None:
            return ConfigResult(
                ip=ip,
                ok=False,
                message=f"post-check failed: APN={apn} PdpType={pdp_type} not found after config",
            )

        final_entries = extract_profile_entries(profile_list_after)
        non_target = [
            (idx, apn_name, pdp)
            for idx, apn_name, pdp in final_entries
            if not (apn_name == apn and (pdp is None or pdp == pdp_type))
        ]
        if non_target:
            details = "; ".join(f"idx={idx},apn={apn_name},pdp={pdp}" for idx, apn_name, pdp in non_target)
            return ConfigResult(
                ip=ip,
                ok=False,
                message=f"post-check failed: non-target profiles remain: {details}",
            )

        if len(final_entries) != 1:
            return ConfigResult(
                ip=ip,
                ok=False,
                message=f"post-check failed: expected 1 profile, found {len(final_entries)}",
            )

        if set_default:
            msg = f"configured: only APN={apn} PdpType={pdp_type} remains at index {verified_index}, default set"
        else:
            msg = f"configured: only APN={apn} PdpType={pdp_type} remains at index {verified_index}"
        return ConfigResult(ip=ip, ok=True, message=msg)
    except Exception as exc:
        return ConfigResult(ip=ip, ok=False, message=str(exc))


def _to_int(value: Any, key: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"Expected integer for '{key}', got {value!r}")


def load_yaml_config(path: str) -> tuple[list[DongleTarget], dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required for --config. Install with: pip install pyyaml") from exc

    cfg_path = Path(path)
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Failed to read config file {cfg_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in {cfg_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("YAML config root must be a mapping")

    defaults_raw = raw.get("defaults", {})
    if defaults_raw is None:
        defaults_raw = {}
    if not isinstance(defaults_raw, dict):
        raise RuntimeError("'defaults' must be a mapping")

    endpoint_default = str(defaults_raw.get("endpoint", DEFAULT_ENDPOINT))
    password_default = defaults_raw.get("password")
    if password_default is not None and not isinstance(password_default, str):
        raise RuntimeError("'defaults.password' must be string or null")

    dongles_raw = raw.get("dongles")
    if not isinstance(dongles_raw, list) or not dongles_raw:
        raise RuntimeError("'dongles' must be a non-empty list")

    targets: list[DongleTarget] = []
    for idx, item in enumerate(dongles_raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"'dongles[{idx}]' must be a mapping")
        ip = item.get("ip")
        if not isinstance(ip, str) or not ip.strip():
            raise RuntimeError(f"'dongles[{idx}].ip' must be a non-empty string")

        endpoint = item.get("endpoint", endpoint_default)
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise RuntimeError(f"'dongles[{idx}].endpoint' must be a non-empty string")

        password = item.get("password", password_default)
        if password is not None and not isinstance(password, str):
            raise RuntimeError(f"'dongles[{idx}].password' must be string or null")

        targets.append(DongleTarget(ip=ip.strip(), endpoint=endpoint.strip(), password=password))

    defaults: dict[str, Any] = {}
    for key in ("apn", "profile_name"):
        val = defaults_raw.get(key)
        if isinstance(val, str) and val.strip():
            defaults[key] = val.strip()
    if "pdp_type" in defaults_raw:
        defaults["pdp_type"] = _to_int(defaults_raw["pdp_type"], "defaults.pdp_type")
    if "set_default" in defaults_raw:
        defaults["set_default"] = bool(defaults_raw["set_default"])

    return targets, defaults


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure APN for five UE dongles.")
    parser.add_argument(
        "--config",
        default=None,
        help=f"YAML config file for dongle IPs/endpoints/passwords (auto: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--ips",
        nargs="+",
        default=DEFAULT_DONGLE_IPS,
        help="Dongle IPs to configure (default: 192.168.101.1..192.168.105.1)",
    )
    parser.add_argument("--endpoint", default=None, help="Dongle JSON-RPC endpoint")
    parser.add_argument("--password", default=None, help="Optional dongle login password")
    parser.add_argument("--apn", default=None, help='APN name to enforce (default: "oai")')
    parser.add_argument(
        "--profile-name",
        default=None,
        help='Profile display name to create (default: "oai")',
    )
    parser.add_argument(
        "--pdp-type",
        type=int,
        default=None,
        choices=[0, 1, 2],
        help="PdpType: 0=IPv4, 1=IPv6, 2=IPv4v6 (default: 0)",
    )
    parser.add_argument(
        "--no-set-default",
        action="store_true",
        help="Only ensure APN profile exists; do not set it as default",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILE,
        help=f"Log file path (default: {DEFAULT_LOG_FILE})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def configure_logging(log_file: str, verbose: bool) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.log_file, args.verbose)

    config_path = args.config
    if not config_path and DEFAULT_CONFIG_FILE.exists():
        config_path = str(DEFAULT_CONFIG_FILE)

    targets: list[DongleTarget]
    cfg_defaults: dict[str, Any] = {}
    if config_path:
        LOGGER.info("Using config file: %s", config_path)
        targets, cfg_defaults = load_yaml_config(config_path)
        if args.endpoint:
            for target in targets:
                target.endpoint = args.endpoint
        if args.password is not None:
            for target in targets:
                target.password = args.password
    else:
        endpoint = args.endpoint or DEFAULT_ENDPOINT
        targets = [DongleTarget(ip=ip, endpoint=endpoint, password=args.password) for ip in args.ips]

    apn = args.apn or str(cfg_defaults.get("apn", "oai"))
    profile_name = args.profile_name or str(cfg_defaults.get("profile_name", "oai"))
    pdp_type = args.pdp_type if args.pdp_type is not None else int(cfg_defaults.get("pdp_type", 0))
    if args.no_set_default:
        set_default = False
    else:
        set_default = bool(cfg_defaults.get("set_default", True))

    LOGGER.info(
        "Configuring dongles: apn=%s pdp_type=%s set_default=%s",
        apn,
        pdp_type,
        set_default,
    )

    results: list[ConfigResult] = []
    for idx, target in enumerate(targets, start=1):
        LOGGER.info("--------------- UE%s (%s) ---------------", idx, target.ip)
        result = configure_one_dongle(
            ip=target.ip,
            endpoint=target.endpoint,
            password=target.password,
            apn=apn,
            profile_name=profile_name,
            pdp_type=pdp_type,
            set_default=set_default,
        )
        results.append(result)
        level = logging.INFO if result.ok else logging.ERROR
        LOGGER.log(level, "[%s] %s", target.ip, result.message)

    success = sum(1 for r in results if r.ok)
    failed = len(results) - success
    print(f"Done: success={success} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

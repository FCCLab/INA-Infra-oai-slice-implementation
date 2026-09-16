#!/usr/bin/env python3
"""
Non-RT rApp — O-RAN A1 Slice SLA (ETSI TS 103 988 / O-RAN.WG2.A1TD).

Operator input is policy type ORAN_SliceSLATarget_3.0.0:
  scope.sliceId {sst, sd, plmnId{mcc,mnc}}
  sliceSlaObjectives {UE/PDU caps, throughput, delay/loss/reliability, jitter/priority/pkt size}
  sliceSlaResources {cellIdList} (optional)

Throughput objectives are mapped to near-RT xApp NS PRB dedicated/min/max.
Other SLA fields are stored and shown; the current xApp does not enforce them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR.parent
DEFAULT_API_HOST = os.environ.get("NWS_RAPP_API_HOST", "0.0.0.0")
DEFAULT_API_PORT = int(os.environ.get("NWS_RAPP_API_PORT", "18090"))
DEFAULT_UI_PORT = int(os.environ.get("NWS_RAPP_UI_PORT", "18091"))
DEFAULT_XAPP = os.environ.get("NWS_XAPP_API_BASE", "http://127.0.0.1:18080").rstrip("/")
DEFAULT_A1_PMS = os.environ.get("NWS_A1_PMS_BASE", "http://policymanagementservice.nonrtric:8081/a1-policy-management/v1").rstrip("/")
DEFAULT_A1_RIC = os.environ.get("NWS_A1_RIC_ID", "nearrt-ric")
DEFAULT_A1_TYPE_ID = os.environ.get("NWS_A1_POLICY_TYPE_ID", "20008")
STORE_PATH = Path(os.environ.get("NWS_RAPP_STORE", str(APP_DIR / "out" / "rapp_store.json")))
NS_DEFAULT_SD = 0xFFFFFF
HISTORY_MAX = 100
POLICY_TYPE_ID = "ORAN_SliceSLATarget_3.0.0"

MCC_RE = re.compile(r"^[0-9]{3}$")
MNC_RE = re.compile(r"^[0-9]{2,3}$")
SD_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

NUMBER_OBJECTIVES = (
    "maxNumberOfUes",
    "maxNumberOfPduSessions",
    "guaDlThptPerSlice",
    "maxDlThptPerSlice",
    "maxDlThptPerUe",
    "guaUlThptPerSlice",
    "maxUlThptPerSlice",
    "maxUlThptPerUe",
    "maxDlPacketDelayPerUe",
    "maxUlPacketDelayPerUe",
    "maxDlPdcpSduPacketLossRatePerUe",
    "maxUlRlcSduPacketLossRatePerUe",
    "maxDlJitterPerUe",
    "maxUlJitterPerUe",
    "dlSlicePriority",
    "ulSlicePriority",
    "maxDlPktSize",
    "maxUlPktSize",
)
LOSS_KEYS = ("maxDlPdcpSduPacketLossRatePerUe", "maxUlRlcSduPacketLossRatePerUe")
PRIORITY_KEYS = ("dlSlicePriority", "ulSlicePriority")
RELIABILITY_KEYS = ("minDlReliabilityPerUe", "minUlReliabilityPerUe")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_sd(value: Any) -> int:
    if isinstance(value, int):
        return value
    s = str(value).strip().lower().replace("0x", "")
    if not s:
        raise ValueError("empty sd")
    return int(s, 16)


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def validate_plmn(raw: Any, prefix: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix} must be an object {{mcc, mnc}}")
    mcc = str(raw.get("mcc", "")).strip()
    mnc = str(raw.get("mnc", "")).strip()
    if not MCC_RE.match(mcc):
        raise ValueError(f"{prefix}.mcc must be 3 digits, got {mcc!r}")
    if not MNC_RE.match(mnc):
        raise ValueError(f"{prefix}.mnc must be 2 or 3 digits, got {mnc!r}")
    return {"mcc": mcc, "mnc": mnc}


def validate_slice_id(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("scope.sliceId must be an object")
    if "sst" not in raw:
        raise ValueError("scope.sliceId.sst is required")
    sst = int(raw["sst"])
    if sst < 0 or sst > 255:
        raise ValueError("scope.sliceId.sst must be 0..255")
    if "plmnId" not in raw:
        raise ValueError("scope.sliceId.plmnId is required")
    out: dict[str, Any] = {
        "sst": sst,
        "plmnId": validate_plmn(raw["plmnId"], "scope.sliceId.plmnId"),
    }
    if raw.get("sd") is not None and str(raw.get("sd")).strip() != "":
        sd_s = str(raw["sd"]).strip().replace("0x", "").replace("0X", "")
        if not SD_RE.match(sd_s):
            raise ValueError("scope.sliceId.sd must be 6 hexadecimal characters")
        if parse_sd(sd_s) == NS_DEFAULT_SD:
            raise ValueError("scope.sliceId.sd=FFFFFF is the default slice and cannot be SET over E2")
        out["sd"] = sd_s.upper()
    extra = set(raw.keys()) - {"sst", "sd", "plmnId"}
    if extra:
        raise ValueError(f"scope.sliceId unknown fields: {sorted(extra)}")
    return out


def validate_reliability(raw: Any, name: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object {{packetSize, userPlaneLatency, successProbability}}")
    required = ("packetSize", "userPlaneLatency", "successProbability")
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"{name} missing {missing}")
    extra = set(raw.keys()) - set(required)
    if extra:
        raise ValueError(f"{name} unknown fields: {sorted(extra)}")
    pkt = _num(raw["packetSize"], f"{name}.packetSize")
    lat = _num(raw["userPlaneLatency"], f"{name}.userPlaneLatency")
    suc = _num(raw["successProbability"], f"{name}.successProbability")
    if pkt < 0 or lat < 0:
        raise ValueError(f"{name} packetSize/userPlaneLatency must be >= 0")
    if suc < 0 or suc > 1:
        raise ValueError(f"{name}.successProbability must be in [0, 1]")
    return {"packetSize": pkt, "userPlaneLatency": lat, "successProbability": suc}


def validate_cell_id(raw: Any, idx: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"sliceSlaResources.cellIdList[{idx}] must be an object")
    cid = raw.get("cId") or raw.get("cid")
    if not isinstance(cid, dict):
        raise ValueError(f"sliceSlaResources.cellIdList[{idx}].cId is required")
    nci = cid.get("ncI", cid.get("nci"))
    if nci is None:
        raise ValueError(f"sliceSlaResources.cellIdList[{idx}].cId.ncI is required")
    return {
        "plmnId": validate_plmn(raw.get("plmnId"), f"sliceSlaResources.cellIdList[{idx}].plmnId"),
        "cId": {"ncI": int(nci)},
    }


def validate_resources(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("sliceSlaResources must be an object")
    extra = set(raw.keys()) - {"cellIdList", "taIList"}
    if extra:
        raise ValueError(f"sliceSlaResources unknown fields: {sorted(extra)}")
    has_cells = "cellIdList" in raw
    has_tai = "taIList" in raw
    if has_cells == has_tai:
        raise ValueError("sliceSlaResources must have exactly one of cellIdList or taIList")
    if has_tai:
        tai = raw["taIList"]
        if not isinstance(tai, list) or not tai:
            raise ValueError("sliceSlaResources.taIList must be a non-empty list")
        return {"taIList": tai}
    cells = raw["cellIdList"]
    if not isinstance(cells, list) or not cells:
        raise ValueError("sliceSlaResources.cellIdList must be a non-empty list")
    return {"cellIdList": [validate_cell_id(c, i) for i, c in enumerate(cells)]}


def validate_objectives(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("sliceSlaObjectives is required and must have at least one attribute")
    allowed = set(NUMBER_OBJECTIVES) | set(RELIABILITY_KEYS)
    extra = set(raw.keys()) - allowed
    if extra:
        raise ValueError(f"sliceSlaObjectives unknown fields: {sorted(extra)}")
    out: dict[str, Any] = {}
    for key in NUMBER_OBJECTIVES:
        if key not in raw:
            continue
        v = _num(raw[key], key)
        if key in LOSS_KEYS and (v < 0 or v > 1):
            raise ValueError(f"{key} must be in [0, 1]")
        if key in PRIORITY_KEYS and v < 1:
            raise ValueError(f"{key} must be >= 1")
        if v < 0:
            raise ValueError(f"{key} must be >= 0")
        out[key] = v
    for key in RELIABILITY_KEYS:
        if key in raw:
            out[key] = validate_reliability(raw[key], key)
    gua_dl, max_dl = out.get("guaDlThptPerSlice"), out.get("maxDlThptPerSlice")
    gua_ul, max_ul = out.get("guaUlThptPerSlice"), out.get("maxUlThptPerSlice")
    if gua_dl is not None and max_dl is not None and gua_dl > max_dl:
        raise ValueError("guaDlThptPerSlice must be <= maxDlThptPerSlice")
    if gua_ul is not None and max_ul is not None and gua_ul > max_ul:
        raise ValueError("guaUlThptPerSlice must be <= maxUlThptPerSlice")
    return out


def validate_a1_policy(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("A1 policy must be a JSON object")
    extra = set(raw.keys()) - {"scope", "sliceSlaObjectives", "sliceSlaResources"}
    if extra:
        raise ValueError(f"unknown top-level fields: {sorted(extra)}")
    scope = raw.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("scope is required")
    if set(scope.keys()) - {"sliceId"}:
        raise ValueError("scope must contain only sliceId (ORAN_SliceSLATarget)")
    if "sliceId" not in scope:
        raise ValueError("scope.sliceId is required")
    policy: dict[str, Any] = {
        "scope": {"sliceId": validate_slice_id(scope["sliceId"])},
        "sliceSlaObjectives": validate_objectives(raw.get("sliceSlaObjectives")),
    }
    if "sliceSlaResources" in raw and raw["sliceSlaResources"] is not None:
        policy["sliceSlaResources"] = validate_resources(raw["sliceSlaResources"])
    return policy


def default_policy_id(policy: dict[str, Any]) -> str:
    """Stable A1 instance id from Open5GS S-NSSAI SD: 000001 → slice-sla-1."""
    sid = (policy.get("scope") or {}).get("sliceId") or {}
    sd = str(sid.get("sd") or "").strip().replace("0x", "").replace("0X", "")
    try:
        n = int(sd, 16)
        if n > 0:
            return f"slice-sla-{n}"
    except ValueError:
        pass
    return str(uuid.uuid4())


def extract_a1_policy(body: dict[str, Any]) -> dict[str, Any]:
    """Accept a raw A1 object or {policy: A1, name?, id?}."""
    if "scope" in body and "sliceSlaObjectives" in body:
        a1 = {k: body[k] for k in ("scope", "sliceSlaObjectives", "sliceSlaResources") if k in body}
        return validate_a1_policy(a1)
    if isinstance(body.get("policy"), dict):
        return validate_a1_policy(body["policy"])
    raise ValueError("body must be an A1 Slice SLA object (scope + sliceSlaObjectives) or {policy: ...}")


EXAMPLE_POLICIES: list[dict[str, Any]] = [
    {
        "id": "ex-ue-pdu",
        "name": "Max UEs / PDU sessions",
        "description": "Fills maxNumberOfUes + maxNumberOfPduSessions only (ETSI TS 103 988 A.9.2).",
        "policy": {
            "sliceSlaObjectives": {"maxNumberOfUes": 100, "maxNumberOfPduSessions": 800},
        },
    },
    {
        "id": "ex-throughput",
        "name": "Guaranteed / max throughput",
        "description": "Fills gua/max DL+UL kbps per slice and per UE only (A.9.1).",
        "policy": {
            "sliceSlaObjectives": {
                "guaDlThptPerSlice": 100000,
                "maxDlThptPerSlice": 300000,
                "maxDlThptPerUe": 50000,
                "guaUlThptPerSlice": 50000,
                "maxUlThptPerSlice": 150000,
                "maxUlThptPerUe": 25000,
            },
        },
    },
    {
        "id": "ex-delay-reliability",
        "name": "Delay / loss / reliability",
        "description": "Fills packet delay, PDCP/RLC loss, min reliability per UE only (A.9.3).",
        "policy": {
            "sliceSlaObjectives": {
                "maxDlPacketDelayPerUe": 10,
                "maxUlPacketDelayPerUe": 10,
                "maxDlPdcpSduPacketLossRatePerUe": 0.01,
                "maxUlRlcSduPacketLossRatePerUe": 0.01,
                "minDlReliabilityPerUe": {
                    "packetSize": 200,
                    "userPlaneLatency": 10,
                    "successProbability": 0.99,
                },
                "minUlReliabilityPerUe": {
                    "packetSize": 200,
                    "userPlaneLatency": 10,
                    "successProbability": 0.99,
                },
            },
        },
    },
    {
        "id": "ex-jitter-priority",
        "name": "Jitter / priority / packet size",
        "description": "Fills jitter, slice priority, max packet size only (A.9.4).",
        "policy": {
            "sliceSlaObjectives": {
                "maxDlJitterPerUe": 2.5,
                "maxUlJitterPerUe": 2.5,
                "dlSlicePriority": 10,
                "ulSlicePriority": 15,
                "maxDlPktSize": 1500,
                "maxUlPktSize": 1500,
            },
        },
    },
    {
        "id": "ex-cell-resources",
        "name": "sliceSlaResources.cellIdList",
        "description": "Fills optional cell scope — NR cell identities (ncI) 101, 102 as CSV.",
        "policy": {
            "sliceSlaResources": {
                "cellIdList": [
                    {"plmnId": {"mcc": "248", "mnc": "35"}, "cId": {"ncI": 101}},
                    {"plmnId": {"mcc": "248", "mnc": "35"}, "cId": {"ncI": 102}},
                ]
            },
        },
    },
]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"policies": [], "history": [], "active": None}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(loaded, dict):
            return
        policies = list(loaded.get("policies") or loaded.get("intents") or [])
        self.data["policies"] = policies
        self.data["history"] = list(loaded.get("history") or [])
        self.data["active"] = loaded.get("active")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def list_policies(self) -> list[dict[str, Any]]:
        return self.snapshot()["policies"]

    def get_policy(self, policy_id: str) -> Optional[dict[str, Any]]:
        for it in self.list_policies():
            if it.get("id") == policy_id:
                return it
        return None

    def upsert_policy(self, rec: dict[str, Any], *, create: bool) -> dict[str, Any]:
        with self.lock:
            items: list[dict[str, Any]] = self.data["policies"]
            idx = next((i for i, it in enumerate(items) if it.get("id") == rec["id"]), None)
            if create and idx is not None:
                raise ValueError(f"policy {rec['id']} already exists")
            if not create and idx is None:
                raise KeyError(rec["id"])
            if idx is None:
                items.append(rec)
            else:
                items[idx] = rec
            self._save()
            return json.loads(json.dumps(rec))

    def delete_policy(self, policy_id: str) -> bool:
        with self.lock:
            before = len(self.data["policies"])
            self.data["policies"] = [it for it in self.data["policies"] if it.get("id") != policy_id]
            if len(self.data["policies"]) == before:
                return False
            if (self.data.get("active") or {}).get("policy_id") == policy_id:
                self.data["active"] = None
            self._save()
            return True

    def add_history(self, entry: dict[str, Any]) -> None:
        with self.lock:
            hist = list(self.data.get("history") or [])
            hist.insert(0, entry)
            self.data["history"] = hist[:HISTORY_MAX]
            if entry.get("ok") and entry.get("action") != "delete":
                self.data["active"] = {
                    "source": entry.get("source"),
                    "at": entry.get("at"),
                    "policy_id": entry.get("policy_id"),
                    "example_id": entry.get("example_id"),
                }
            elif entry.get("action") == "delete" and (self.data.get("active") or {}).get("policy_id") == entry.get("policy_id"):
                self.data["active"] = None
            self._save()


class XappClient:
    def __init__(self, base: str, timeout_s: float = 10.0) -> None:
        self.base = base.rstrip("/")
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                parsed: Any = {}
                if raw.strip():
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = {"raw": raw}
                return int(resp.status), parsed
        except HTTPError as e:
            raw = e.read().decode("utf-8", errors="ignore") if e.fp else ""
            parsed = {}
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw": raw}
            return int(e.code), parsed
        except (URLError, TimeoutError, OSError) as e:
            raise ConnectionError(f"xApp not reachable at {self.base}: {e}") from e

    def health(self) -> dict[str, Any]:
        try:
            code, body = self._request("GET", "/health")
            ok = 200 <= code < 300
            return {"ok": ok, "status_code": code, "body": body, "base": self.base}
        except ConnectionError as e:
            return {"ok": False, "status_code": 0, "error": str(e), "base": self.base}

    def get_slices(self) -> dict[str, Any]:
        code, body = self._request("GET", "/api/v1/slices")
        if not (200 <= code < 300):
            raise RuntimeError(f"xApp GET slices HTTP {code}: {body}")
        if not isinstance(body, dict):
            return {"slices": body}
        return body

    def put_slices(self, slices: list[dict[str, Any]]) -> dict[str, Any]:
        code, body = self._request("PUT", "/api/v1/slices", {"slices": slices})
        if not (200 <= code < 300):
            err = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"xApp PUT slices HTTP {code}: {err}")
        if not isinstance(body, dict):
            return {"ok": True, "xapp": body}
        return body

    def get_slice_sla(self) -> dict[str, Any]:
        code, body = self._request("GET", "/api/v1/a1/slice-sla")
        if not (200 <= code < 300):
            raise RuntimeError(f"xApp GET A1 SLA HTTP {code}: {body}")
        if not isinstance(body, dict):
            return {"policies": body}
        return body

    def put_slice_sla(self, policy: dict[str, Any], policy_id: Optional[str] = None) -> dict[str, Any]:
        payload: Any = {"id": policy_id, "policy": policy} if policy_id else policy
        code, body = self._request("PUT", "/api/v1/a1/slice-sla", payload)
        if not (200 <= code < 300):
            err = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"xApp PUT A1 SLA HTTP {code}: {err}")
        if not isinstance(body, dict):
            return {"ok": True, "xapp": body}
        return body

    def delete_slice_sla(self, key: str) -> dict[str, Any]:
        code, body = self._request("DELETE", f"/api/v1/a1/slice-sla/{quote(key, safe='')}")
        if code == 404:
            return {"ok": True, "status_code": 404, "note": "not on xApp"}
        if not (200 <= code < 300):
            err = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"xApp DELETE A1 SLA HTTP {code}: {err}")
        return body if isinstance(body, dict) else {"ok": True, "status_code": code}


class A1PmsClient:
    def __init__(self, base: str, ric_id: str = DEFAULT_A1_RIC, type_id: str = DEFAULT_A1_TYPE_ID, timeout_s: float = 10.0) -> None:
        self.base = base.rstrip("/")
        self.ric_id = ric_id
        self.type_id = type_id
        self.timeout_s = timeout_s
        # Create-or-update uses A1-PMS v2 PUT (/a1-policy/v2). v1 POST returns 409
        # when the policy id already exists and cannot update the body.
        if "/a1-policy-management/v1" in self.base:
            self.v2_base = self.base.replace("/a1-policy-management/v1", "/a1-policy/v2")
        elif self.base.endswith("/a1-policy/v2"):
            self.v2_base = self.base
        else:
            self.v2_base = self.base.rstrip("/") + "/a1-policy/v2"

    def _request(self, method: str, path: str, body: Any = None, *, base: Optional[str] = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        # DELETE often returns 204 with an empty body; Accept: application/json then
        # yields HTTP 406 from A1-PMS. Prefer */* so empty success responses work.
        headers = {"Accept": "*/*"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(
            (base or self.base) + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                parsed: Any = {}
                if raw.strip():
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = {"raw": raw}
                return int(resp.status), parsed
        except HTTPError as e:
            raw = e.read().decode("utf-8", errors="ignore") if e.fp else ""
            parsed = {}
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {"raw": raw}
            return int(e.code), parsed
        except (URLError, TimeoutError, OSError) as e:
            raise ConnectionError(f"A1 PMS not reachable at {self.base}: {e}") from e

    def health(self) -> dict[str, Any]:
        try:
            code, body = self._request("GET", f"/rics?policyTypeId={self.type_id}")
            ok = 200 <= code < 300
            return {
                "ok": ok,
                "status_code": code,
                "body": body,
                "base": self.base,
                "ric_id": self.ric_id,
                "type_id": self.type_id,
            }
        except ConnectionError as e:
            return {
                "ok": False,
                "status_code": 0,
                "error": str(e),
                "base": self.base,
                "ric_id": self.ric_id,
                "type_id": self.type_id,
            }

    def get_policies(self) -> list[Any]:
        code, body = self._request("GET", f"/policies?nearRtRicId={self.ric_id}&policyTypeId={self.type_id}")
        if not (200 <= code < 300):
            raise RuntimeError(f"A1 PMS GET policies HTTP {code}: {body}")
        return body if isinstance(body, list) else []

    def get_policy(self, policy_id: str) -> Any:
        code, body = self._request("GET", f"/policies/{policy_id}")
        if not (200 <= code < 300):
            raise RuntimeError(f"A1 PMS GET policy {policy_id} HTTP {code}: {body}")
        return body

    def get_policy_status(self, policy_id: str) -> dict[str, Any]:
        code, body = self._request("GET", f"/policies/{policy_id}/status")
        if not (200 <= code < 300):
            raise RuntimeError(f"A1 PMS GET policy {policy_id} status HTTP {code}: {body}")
        return body if isinstance(body, dict) else {"raw": body}

    def list_active_policies(self) -> list[dict[str, Any]]:
        """Live A1 instances on this RIC/type, with body + enforce status."""
        out: list[dict[str, Any]] = []
        for item in self.get_policies():
            if isinstance(item, dict):
                pid = str(item.get("policyId") or item.get("policy_id") or "")
                ric = str(item.get("nearRtRicId") or item.get("ric_id") or self.ric_id)
            else:
                pid = str(item)
                ric = self.ric_id
            if not pid:
                continue
            rec: dict[str, Any] = {
                "policy_id": pid,
                "ric_id": ric,
                "policytype_id": self.type_id,
                "service_id": None,
                "policy": None,
                "status": {},
            }
            try:
                body = self.get_policy(pid)
                if isinstance(body, dict) and ("scope" in body or "sliceSlaObjectives" in body):
                    rec["policy"] = body
                    rec["service_id"] = body.get("service_id")
                elif isinstance(body, dict) and "policy_data" in body:
                    rec["policy"] = body.get("policy_data")
                    rec["service_id"] = body.get("service_id")
                    rec["ric_id"] = body.get("ric_id") or ric
                    rec["policytype_id"] = body.get("policytype_id") or self.type_id
                else:
                    rec["policy"] = body
            except Exception as e:
                rec["policy_error"] = str(e)
            try:
                rec["status"] = self.get_policy_status(pid)
            except Exception as e:
                rec["status_error"] = str(e)
            out.append(rec)
        return out

    def put_policy(self, policy_id: str, policy_object: dict[str, Any]) -> dict[str, Any]:
        """Create or update an A1 policy instance (idempotent).

        Uses A1-PMS v2 PUT with policy_data. v1 POST only creates and returns
        HTTP 409 when the same policy_id already exists, so Apply on an existing
        Slice SLA would otherwise leave PMS/a1mediator unchanged.
        """
        payload = {
            "ric_id": self.ric_id,
            "policy_id": policy_id,
            "service_id": "nws-rapp",
            "policytype_id": self.type_id,
            "policy_data": policy_object,
        }
        code, body = self._request("PUT", "/policies", payload, base=self.v2_base)
        if not (200 <= code < 300):
            err = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"A1 PMS PUT policy HTTP {code}: {err}")
        return body if isinstance(body, dict) else {"ok": True, "pms": body, "status_code": code}

    def delete_policy(self, policy_id: str) -> dict[str, Any]:
        code, body = self._request("DELETE", f"/policies/{policy_id}")
        # A1-PMS returns 204 No Content on success.
        if not (200 <= code < 300 or code == 204 or code == 404):
            err = body.get("error") if isinstance(body, dict) else body
            raise RuntimeError(f"A1 PMS DELETE policy HTTP {code}: {err}")
        return {"ok": True, "status_code": code}


def make_record(body: dict[str, Any], *, policy: dict[str, Any], existing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    now = utc_now()
    rec_id = str(
        body.get("id")
        or body.get("policy_id")
        or (existing or {}).get("id")
        or default_policy_id(policy)
    ).strip()
    name = str(body.get("name") or (existing or {}).get("name") or "").strip()
    if not name:
        sid = policy["scope"]["sliceId"]
        name = f"SST {sid['sst']} SD {sid.get('sd', '—')}"
    return {
        "id": rec_id,
        "name": name,
        "description": str(body.get("description") or (existing or {}).get("description") or ""),
        "policy_type_id": POLICY_TYPE_ID,
        "policy": policy,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }


class RappController:
    def __init__(self, store: Store, xapp: XappClient, a1_pms: Optional[A1PmsClient] = None) -> None:
        self.store = store
        self.xapp = xapp
        self.a1_pms = a1_pms

    def status(self) -> dict[str, Any]:
        xh = self.xapp.health()
        xapp_sla: Any = None
        if xh.get("ok"):
            try:
                xapp_sla = self.xapp.get_slice_sla()
            except Exception as e:
                xh["sla_error"] = str(e)
        pms_h = self.a1_pms.health() if self.a1_pms else {"ok": False, "error": "A1 PMS not configured"}
        snap = self.store.snapshot()
        return {
            "status": "ok",
            "policy_type_id": POLICY_TYPE_ID,
            "a1_policy_type_id": self.a1_pms.type_id if self.a1_pms else DEFAULT_A1_TYPE_ID,
            "rapp": {"policies": len(snap["policies"]), "history": len(snap["history"])},
            "a1_pms": pms_h,
            "xapp": xh,
            "xapp_sla": xapp_sla,
            "active": snap.get("active"),
            "docs": "/docs",
            "console": f":{DEFAULT_UI_PORT}",
        }

    def active_a1_policies(self) -> dict[str, Any]:
        """Policies actually installed on A1-PMS for this RIC/type (not xApp leftovers)."""
        pms_error = None
        pms_policies: list[dict[str, Any]] = []
        if self.a1_pms:
            try:
                pms_policies = self.a1_pms.list_active_policies()
            except Exception as e:
                pms_error = str(e)
        else:
            pms_error = "A1 PMS not configured"

        xapp_policies: list[dict[str, Any]] = []
        try:
            sla = self.xapp.get_slice_sla()
            if isinstance(sla, dict):
                xapp_policies = list(sla.get("policies") or [])
        except Exception:
            xapp_policies = []

        def slice_key(policy: Any) -> str:
            if not isinstance(policy, dict):
                return ""
            sid = ((policy.get("scope") or {}).get("sliceId") or {})
            plmn = sid.get("plmnId") or {}
            sd = str(sid.get("sd") or "").upper().replace("0X", "")
            return f"{plmn.get('mcc', '')}-{plmn.get('mnc', '')}-{sid.get('sst', '')}-{sd}"

        xapp_by_key: dict[str, dict[str, Any]] = {}
        for xp in xapp_policies:
            if not isinstance(xp, dict):
                continue
            key = str(xp.get("key") or slice_key(xp.get("policy")) or "")
            if key:
                xapp_by_key[key] = xp

        for rec in pms_policies:
            key = slice_key(rec.get("policy"))
            rec["slice_key"] = key
            xp = xapp_by_key.get(key)
            rec["on_xapp"] = xp is not None
            if xp:
                rec["xapp_key"] = xp.get("key")
                rec["xapp_received_at"] = xp.get("received_at")

        return {
            "ok": pms_error is None,
            "error": pms_error,
            "ric_id": self.a1_pms.ric_id if self.a1_pms else None,
            "policytype_id": self.a1_pms.type_id if self.a1_pms else DEFAULT_A1_TYPE_ID,
            "count": len(pms_policies),
            "policies": pms_policies,
        }

    def delete_active_policy(self, policy_id: str) -> dict[str, Any]:
        """Remove a live A1 instance from A1-PMS (and xApp cache if present)."""
        pid = str(policy_id or "").strip()
        if not pid:
            raise ValueError("policy_id is required")
        if not self.a1_pms:
            raise RuntimeError("A1 PMS not configured")

        rec = next((p for p in self.active_a1_policies().get("policies") or [] if p.get("policy_id") == pid), None)
        xapp_key = (rec or {}).get("xapp_key") or (rec or {}).get("slice_key")

        pms = self.a1_pms.delete_policy(pid)

        xapp: Any = None
        xapp_error = None
        if xapp_key:
            try:
                xapp = self.xapp.delete_slice_sla(str(xapp_key))
            except Exception as e:
                xapp_error = str(e)

        self.store.add_history(
            {
                "at": utc_now(),
                "ok": True,
                "action": "delete",
                "source": f"delete:{pid}",
                "policy_id": pid,
                "policy": (rec or {}).get("policy"),
                "pms": pms,
                "pms_error": None,
                "xapp": xapp,
                "xapp_error": xapp_error,
            }
        )
        return {
            "ok": True,
            "deleted": pid,
            "pms": pms,
            "xapp": xapp,
            "xapp_error": xapp_error,
        }

    def results(self) -> dict[str, Any]:
        """Consolidated network slicing operational results across rApp, A1 PMS, and xApp."""
        slices_raw: list[dict[str, Any]] = []
        try:
            xapp_slices = self.xapp.get_slices()
            if isinstance(xapp_slices, dict):
                slices_raw = xapp_slices.get("slices", [])
        except Exception:
            slices_raw = []

        policies_raw: list[dict[str, Any]] = []
        try:
            xapp_sla = self.xapp.get_slice_sla()
            if isinstance(xapp_sla, dict):
                policies_raw = xapp_sla.get("policies", [])
        except Exception:
            policies_raw = []

        sla_by_slice: dict[str, dict[str, Any]] = {}
        for item in policies_raw:
            p = item.get("policy", {}) if isinstance(item, dict) else {}
            scope = p.get("scope", {}).get("sliceId", {})
            sst = scope.get("sst")
            sd = str(scope.get("sd", "")).lower().replace("0x", "")
            if sst is not None:
                sla_by_slice[f"{sst}-{sd}"] = p

        enriched_slices: list[dict[str, Any]] = []
        total_ded = 0.0
        total_min = 0.0
        for s in slices_raw:
            sst = s.get("sst")
            sd_norm = str(s.get("sd", "")).lower().replace("0x", "")
            ded = float(s.get("dedicated", 0.0) or 0.0)
            min_p = float(s.get("min", 0.0) or 0.0)
            max_p = float(s.get("max", 100.0) or 100.0)
            total_ded += ded
            total_min += min_p

            matched_sla = sla_by_slice.get(f"{sst}-{sd_norm}")
            enriched_slices.append({
                "sst": sst,
                "sd": s.get("sd"),
                "direction": s.get("direction", "dl"),
                "dedicated_prb_pct": ded,
                "min_prb_pct": min_p,
                "max_prb_pct": max_p,
                "a1_policy": matched_sla,
                "has_a1_sla": matched_sla is not None,
                "status": "active",
            })

        snap = self.store.snapshot()
        recent = snap.get("history", [])[-10:]
        pms_h = self.a1_pms.health() if self.a1_pms else {"ok": False}
        xh = self.xapp.health()

        return {
            "status": "ok",
            "timestamp": utc_now(),
            "summary": {
                "total_slices": len(enriched_slices),
                "total_dedicated_prb_pct": round(total_ded, 2),
                "total_min_prb_pct": round(total_min, 2),
                "shared_prb_pool_pct": round(max(0.0, 100.0 - total_ded), 2),
                "active_a1_policies": len(policies_raw),
                "pms_connected": bool(pms_h.get("ok")),
                "xapp_connected": bool(xh.get("ok")),
            },
            "slices": enriched_slices,
            "a1_policies": policies_raw,
            "pms": pms_h,
            "xapp": xh,
            "recent_enforcements": recent,
        }

    def preview(self, body: dict[str, Any]) -> dict[str, Any]:
        policy = extract_a1_policy(body)
        return {"ok": True, "policy": policy}

    def create_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        policy = extract_a1_policy(body)
        rec = make_record(body, policy=policy)
        existing = self.store.get_policy(rec["id"])
        if existing is not None:
            rec["created_at"] = existing.get("created_at") or rec["created_at"]
            return self.store.upsert_policy(rec, create=False)
        return self.store.upsert_policy(rec, create=True)

    def update_policy(self, policy_id: str, body: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get_policy(policy_id)
        if current is None:
            raise KeyError(policy_id)
        merged = dict(body)
        merged.setdefault("id", policy_id)
        if "scope" not in merged and "policy" not in merged:
            merged["policy"] = current["policy"]
        policy = extract_a1_policy(merged) if ("scope" in merged or "policy" in merged) else current["policy"]
        rec = make_record(merged, policy=policy, existing=current)
        rec["id"] = policy_id
        return self.store.upsert_policy(rec, create=False)

    def put_a1_instance(self, policy_id: str, body: dict[str, Any]) -> dict[str, Any]:
        policy = validate_a1_policy(body)
        existing = self.store.get_policy(policy_id)
        rec = make_record({"id": policy_id}, policy=policy, existing=existing)
        return self.store.upsert_policy(rec, create=existing is None)

    def activate_record(self, rec: dict[str, Any], *, source: str, example_id: Optional[str] = None) -> dict[str, Any]:
        # Keep rApp library in sync with what we push southbound.
        existing = self.store.get_policy(str(rec.get("id") or ""))
        self.store.upsert_policy(rec, create=existing is None)

        pms_resp = None
        pms_error = None
        if self.a1_pms:
            try:
                pms_resp = self.a1_pms.put_policy(rec["id"], rec["policy"])
            except Exception as e:
                pms_error = str(e)

        xapp_resp = None
        xapp_error = None
        try:
            xapp_resp = self.xapp.put_slice_sla(rec["policy"], policy_id=rec.get("id"))
        except Exception as e:
            xapp_error = str(e)
            if not pms_resp:
                raise RuntimeError(f"A1 PMS error: {pms_error or 'none'}; xApp direct error: {e}")
            xapp_resp = {"direct_xapp_skipped_or_failed": str(e)}

        ok = pms_error is None and xapp_error is None
        entry = {
            "at": utc_now(),
            "ok": ok,
            "source": source,
            "policy_id": rec.get("id"),
            "example_id": example_id,
            "policy": rec["policy"],
            "pms": pms_resp,
            "pms_error": pms_error,
            "xapp": xapp_resp,
            "xapp_error": xapp_error,
        }
        self.store.add_history(entry)
        return entry

    def activate_id(self, policy_id: str) -> dict[str, Any]:
        rec = self.store.get_policy(policy_id)
        if rec is None:
            raise KeyError(policy_id)
        return self.activate_record(rec, source=f"policy:{policy_id}")

    def activate_example(self, example_id: str) -> dict[str, Any]:
        ex = next((e for e in EXAMPLE_POLICIES if e["id"] == example_id), None)
        if ex is None:
            raise KeyError(example_id)
        raw = ex["policy"]
        if "scope" not in raw or "sliceSlaObjectives" not in raw:
            raise ValueError(
                f"{example_id} is a field group only; compose a full A1 policy in the console "
                "and POST /api/v1/activate"
            )
        rec = make_record(
            {"id": example_id, "name": ex["name"], "description": ex["description"]},
            policy=validate_a1_policy(raw),
        )
        return self.activate_record(rec, source=f"example:{example_id}", example_id=example_id)

    def activate_adhoc(self, body: dict[str, Any]) -> dict[str, Any]:
        policy = extract_a1_policy(body)
        rec = make_record(body, policy=policy)
        return self.activate_record(rec, source="adhoc")


A1_POLICY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scope", "sliceSlaObjectives"],
    "additionalProperties": False,
    "properties": {
        "scope": {
            "type": "object",
            "required": ["sliceId"],
            "additionalProperties": False,
            "properties": {
                "sliceId": {
                    "type": "object",
                    "required": ["sst", "plmnId"],
                    "properties": {
                        "sst": {"type": "integer", "minimum": 0, "maximum": 255, "example": 1},
                        "sd": {"type": "string", "pattern": "^[0-9A-Fa-f]{6}$", "example": "456DEF"},
                        "plmnId": {
                            "type": "object",
                            "required": ["mcc", "mnc"],
                            "properties": {
                                "mcc": {"type": "string", "pattern": "^[0-9]{3}$", "example": "248"},
                                "mnc": {"type": "string", "pattern": "^[0-9]{2,3}$", "example": "35"},
                            },
                        },
                    },
                }
            },
        },
        "sliceSlaObjectives": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": False,
            "properties": {
                "maxNumberOfUes": {"type": "number", "minimum": 0},
                "maxNumberOfPduSessions": {"type": "number", "minimum": 0},
                "guaDlThptPerSlice": {"type": "number", "minimum": 0, "description": "kbps"},
                "maxDlThptPerSlice": {"type": "number", "minimum": 0, "description": "kbps"},
                "maxDlThptPerUe": {"type": "number", "minimum": 0, "description": "kbps"},
                "guaUlThptPerSlice": {"type": "number", "minimum": 0, "description": "kbps"},
                "maxUlThptPerSlice": {"type": "number", "minimum": 0, "description": "kbps"},
                "maxUlThptPerUe": {"type": "number", "minimum": 0, "description": "kbps"},
                "maxDlPacketDelayPerUe": {"type": "number", "minimum": 0},
                "maxUlPacketDelayPerUe": {"type": "number", "minimum": 0},
                "maxDlPdcpSduPacketLossRatePerUe": {"type": "number", "minimum": 0, "maximum": 1},
                "maxUlRlcSduPacketLossRatePerUe": {"type": "number", "minimum": 0, "maximum": 1},
                "minDlReliabilityPerUe": {"$ref": "#/components/schemas/ReliabilityType"},
                "minUlReliabilityPerUe": {"$ref": "#/components/schemas/ReliabilityType"},
                "maxDlJitterPerUe": {"type": "number", "minimum": 0},
                "maxUlJitterPerUe": {"type": "number", "minimum": 0},
                "dlSlicePriority": {"type": "number", "minimum": 1},
                "ulSlicePriority": {"type": "number", "minimum": 1},
                "maxDlPktSize": {"type": "number", "minimum": 0},
                "maxUlPktSize": {"type": "number", "minimum": 0},
            },
        },
        "sliceSlaResources": {
            "type": "object",
            "properties": {
                "cellIdList": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["plmnId", "cId"],
                        "properties": {
                            "plmnId": {"$ref": "#/components/schemas/PlmnId"},
                            "cId": {
                                "type": "object",
                                "required": ["ncI"],
                                "properties": {"ncI": {"type": "integer"}},
                            },
                        },
                    },
                }
            },
        },
    },
}

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "nws O-RAN Slice SLA rApp",
        "version": "2.0.0",
        "description": (
            "Non-RT rApp for O-RAN A1 policy type **ORAN_SliceSLATarget_3.0.0** "
            "(ETSI TS 103 988 / O-RAN.WG2.A1TD).\n\n"
            "User input is `scope.sliceId` + `sliceSlaObjectives` "
            "(+ optional `sliceSlaResources`). "
            "Activate **PUTs the A1 object as-is** to the near-RT xApp "
            "`/api/v1/a1/slice-sla` (not PRB %)."
        ),
    },
    "servers": [{"url": "/", "description": "this rApp"}],
    "tags": [
        {"name": "health", "description": "Liveness"},
        {"name": "a1", "description": "O-RAN A1 Slice SLA policies"},
        {"name": "xapp", "description": "Near-RT xApp proxy"},
    ],
    "paths": {
        "/health": {"get": {"tags": ["health"], "summary": "Health", "responses": {"200": {"description": "OK"}}}},
        "/api/v1/results": {"get": {"tags": ["a1"], "summary": "Consolidated network slicing operational results (E2 PRB allocations + A1 SLAs)", "responses": {"200": {"description": "Results"}}}},
        "/api/v1/status": {"get": {"tags": ["health"], "summary": "rApp + xApp status", "responses": {"200": {"description": "Status"}}}},
        "/api/v1/examples": {
            "get": {
                "tags": ["a1"],
                "summary": "Built-in A1 Slice SLA examples",
                "responses": {"200": {"description": "Examples"}},
            }
        },
        "/api/v1/policies": {
            "get": {"tags": ["a1"], "summary": "List saved A1 policies", "responses": {"200": {"description": "Policies"}}},
            "post": {
                "tags": ["a1"],
                "summary": "Save an A1 Slice SLA policy",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/PolicyWrite"},
                            "examples": {
                                "uePdu": {
                                    "summary": "Max UEs / PDU sessions",
                                    "value": EXAMPLE_POLICIES[0]["policy"],
                                },
                                "thpt": {
                                    "summary": "Throughput SLA",
                                    "value": EXAMPLE_POLICIES[1]["policy"],
                                },
                                "delay": {
                                    "summary": "Delay / reliability",
                                    "value": EXAMPLE_POLICIES[2]["policy"],
                                },
                                "jitter": {
                                    "summary": "Jitter / priority / packet size",
                                    "value": EXAMPLE_POLICIES[3]["policy"],
                                },
                                "cells": {
                                    "summary": "cellIdList resources",
                                    "value": EXAMPLE_POLICIES[4]["policy"],
                                },
                            },
                        }
                    },
                },
                "responses": {"200": {"description": "Saved"}, "400": {"description": "Validation"}},
            },
        },
        "/api/v1/policies/preview": {
            "post": {
                "tags": ["a1"],
                "summary": "Validate A1 policy and preview PRB translation",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A1SliceSlaPolicy"}}},
                },
                "responses": {"200": {"description": "Preview"}, "400": {"description": "Validation"}},
            }
        },
        "/api/v1/policies/{id}": {
            "get": {
                "tags": ["a1"],
                "summary": "Get saved policy",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Policy"}, "404": {"description": "Not found"}},
            },
            "put": {
                "tags": ["a1"],
                "summary": "Replace saved policy",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PolicyWrite"}}},
                },
                "responses": {"200": {"description": "Updated"}},
            },
            "delete": {
                "tags": ["a1"],
                "summary": "Delete saved policy",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Deleted"}},
            },
        },
        "/api/v1/policies/{id}/activate": {
            "post": {
                "tags": ["a1"],
                "summary": "Activate saved policy (E2 SET if throughput maps to PRB)",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Activated"}, "502": {"description": "xApp down"}},
            }
        },
        "/api/v1/examples/{id}/activate": {
            "post": {
                "tags": ["a1"],
                "summary": "Activate a built-in A1 example",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Activated"}},
            }
        },
        "/api/v1/activate": {
            "post": {
                "tags": ["a1"],
                "summary": "Ad-hoc activate a raw A1 Slice SLA object",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A1SliceSlaPolicy"}}},
                },
                "responses": {"200": {"description": "Activated"}},
            }
        },
        "/A1-P/v2/policytypes": {
            "get": {"tags": ["a1"], "summary": "A1 policy types", "responses": {"200": {"description": "Type ids"}}}
        },
        "/A1-P/v2/policytypes/{type_id}/policies/{policy_id}": {
            "put": {
                "tags": ["a1"],
                "summary": "A1-P PUT policy instance (raw Slice SLA body)",
                "parameters": [
                    {"name": "type_id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "policy_id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A1SliceSlaPolicy"}}},
                },
                "responses": {"200": {"description": "Stored"}},
            }
        },
        "/api/v1/xapp/slices": {
            "get": {"tags": ["xapp"], "summary": "Proxy GET xApp NS policy", "responses": {"200": {"description": "Snapshot"}}}
        },
        "/api/v1/a1/policies": {
            "get": {
                "tags": ["a1"],
                "summary": "Live A1-PMS policies for the configured RIC/type (bodies + enforce status)",
                "responses": {"200": {"description": "Active policies"}},
            }
        },
        "/api/v1/a1/policies/{id}": {
            "delete": {
                "tags": ["a1"],
                "summary": "Delete a live A1-PMS policy instance (propagates to Near-RT RIC)",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Deleted"}, "502": {"description": "A1-PMS error"}},
            }
        },
        "/api/v1/history": {"get": {"tags": ["a1"], "summary": "Activation history", "responses": {"200": {"description": "History"}}}},
        "/docs": {"get": {"tags": ["health"], "summary": "Swagger UI", "responses": {"200": {"description": "HTML"}}}},
        "/openapi.json": {"get": {"tags": ["health"], "summary": "OpenAPI JSON", "responses": {"200": {"description": "OpenAPI"}}}},
    },
    "components": {
        "schemas": {
            "PlmnId": {
                "type": "object",
                "required": ["mcc", "mnc"],
                "properties": {
                    "mcc": {"type": "string", "example": "248"},
                    "mnc": {"type": "string", "example": "35"},
                },
            },
            "ReliabilityType": {
                "type": "object",
                "required": ["packetSize", "userPlaneLatency", "successProbability"],
                "properties": {
                    "packetSize": {"type": "number"},
                    "userPlaneLatency": {"type": "number"},
                    "successProbability": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
            "A1SliceSlaPolicy": A1_POLICY_SCHEMA,
            "PolicyWrite": {
                "oneOf": [
                    {"$ref": "#/components/schemas/A1SliceSlaPolicy"},
                    {
                        "type": "object",
                        "required": ["policy"],
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "policy": {"$ref": "#/components/schemas/A1SliceSlaPolicy"},
                        },
                    },
                ]
            },
        }
    },
}

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>nws O-RAN Slice SLA rApp — Swagger UI</title>
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


def _collect_host_ips() -> list[dict[str, str]]:
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
            iface, addr = parts[1], parts[3].split("/")[0]
            if addr and not addr.startswith("127."):
                found.setdefault(addr, iface)
    except (OSError, subprocess.SubprocessError):
        pass
    rows = [{"ip": ip, "iface": iface} for ip, iface in sorted(found.items())]
    return [r for r in rows if not r["ip"].startswith("10.244.")]


def _hosts_payload(api_port: int) -> dict[str, Any]:
    ui_port = DEFAULT_UI_PORT
    rows = _collect_host_ips() or [{"ip": "127.0.0.1", "iface": "loopback"}]
    return {
        "port": api_port,
        "console_port": ui_port,
        "base_urls": [
            {
                "ip": r["ip"],
                "iface": r.get("iface", ""),
                "api": f"http://{r['ip']}:{api_port}/api/v1/status",
                "docs": f"http://{r['ip']}:{api_port}/docs",
                "console": f"http://{r['ip']}:{ui_port}/",
            }
            for r in rows
        ],
    }


class RappApiHandler(BaseHTTPRequestHandler):
    controller: Optional[RappController] = None

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
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, POST, DELETE, OPTIONS")
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

    def _ctrl(self) -> RappController:
        if self.controller is None:
            raise RuntimeError("controller not ready")
        return self.controller

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path in ("/docs", "/swagger"):
                self._send(200, SWAGGER_HTML, content_type="text/html; charset=utf-8")
                return
            if path == "/openapi.json":
                self._send(200, OPENAPI_SPEC)
                return
            if path == "/api/v1/hosts":
                self._send(200, _hosts_payload(int(self.server.server_address[1])))
                return
            if path in ("/", "/health"):
                xh = self._ctrl().xapp.health()
                self._send(
                    200,
                    {
                        "status": "ok",
                        "policy_type_id": POLICY_TYPE_ID,
                        "xapp_ok": bool(xh.get("ok")),
                        "docs": "/docs",
                        "console": f":{DEFAULT_UI_PORT}",
                    },
                )
                return
            if path in ("/api/v1/results", "/api/v1/slices/results"):
                self._send(200, self._ctrl().results())
                return
            if path == "/api/v1/status":
                self._send(200, self._ctrl().status())
                return
            if path == "/api/v1/examples":
                self._send(200, {"policy_type_id": POLICY_TYPE_ID, "examples": EXAMPLE_POLICIES})
                return
            if path in ("/api/v1/policies", "/api/v1/intents"):
                self._send(200, {"policies": self._ctrl().store.list_policies()})
                return
            if path.startswith("/api/v1/policies/") or path.startswith("/api/v1/intents/"):
                prefix = "/api/v1/policies/" if path.startswith("/api/v1/policies/") else "/api/v1/intents/"
                rest = path[len(prefix) :]
                if "/" in rest:
                    self._send(404, {"error": "not found"})
                    return
                rec = self._ctrl().store.get_policy(unquote(rest))
                if rec is None:
                    self._send(404, {"error": f"policy {rest} not found"})
                    return
                self._send(200, rec)
                return
            if path in ("/api/v1/a1/policies", "/api/v1/active-policies"):
                self._send(200, self._ctrl().active_a1_policies())
                return
            if path == "/api/v1/xapp/slice-sla":
                self._send(200, self._ctrl().xapp.get_slice_sla())
                return
            if path == "/api/v1/xapp/slices":
                self._send(200, self._ctrl().xapp.get_slices())
                return
            if path == "/api/v1/history":
                self._send(200, {"history": self._ctrl().store.snapshot()["history"]})
                return
            if path in ("/A1-P/v2/policytypes", "/a1-p/v2/policytypes"):
                self._send(200, [POLICY_TYPE_ID])
                return
            if path in (
                f"/A1-P/v2/policytypes/{POLICY_TYPE_ID}",
                f"/a1-p/v2/policytypes/{POLICY_TYPE_ID}",
            ):
                self._send(200, {"policy_type_id": POLICY_TYPE_ID, "schema": A1_POLICY_SCHEMA})
                return
            if path.endswith("/policies") and "policytypes/" in path:
                ids = [p["id"] for p in self._ctrl().store.list_policies()]
                self._send(200, ids)
                return
            self._send(
                404,
                {
                    "error": "not found",
                    "docs": "/docs",
                    "policy_type_id": POLICY_TYPE_ID,
                    "endpoints": {
                        "GET /api/v1/examples": "built-in A1 Slice SLA examples",
                        "GET /api/v1/a1/policies": "live A1-PMS policies for this RIC",
                        "DELETE /api/v1/a1/policies/{id}": "delete live A1-PMS policy instance",
                        "POST /api/v1/policies": "save A1 policy (raw or {name,policy})",
                        "POST /api/v1/policies/preview": "validate + PRB translation",
                        "POST /api/v1/policies/{id}/activate": "activate saved policy",
                        "POST /api/v1/activate": "ad-hoc A1 activate",
                        "PUT /A1-P/v2/policytypes/ORAN_SliceSLATarget_3.0.0/policies/{id}": "A1-P PUT",
                    },
                },
            )
        except ConnectionError as e:
            self._send(502, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            body = self._read_json() if int(self.headers.get("Content-Length") or "0") else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            if path in ("/api/v1/policies", "/api/v1/intents"):
                self._send(200, self._ctrl().create_policy(body))
                return
            if path == "/api/v1/policies/preview":
                self._send(200, self._ctrl().preview(body))
                return
            if path == "/api/v1/activate":
                self._send(200, self._ctrl().activate_adhoc(body))
                return
            if path.startswith("/api/v1/examples/") and path.endswith("/activate"):
                eid = unquote(path[len("/api/v1/examples/") : -len("/activate")])
                self._send(200, self._ctrl().activate_example(eid))
                return
            if (path.startswith("/api/v1/policies/") or path.startswith("/api/v1/intents/")) and path.endswith("/activate"):
                prefix = "/api/v1/policies/" if path.startswith("/api/v1/policies/") else "/api/v1/intents/"
                pid = unquote(path[len(prefix) : -len("/activate")])
                self._send(200, self._ctrl().activate_id(pid))
                return
            self._send(404, {"error": "not found", "docs": "/docs"})
        except KeyError as e:
            self._send(404, {"error": f"not found: {e}"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except ConnectionError as e:
            self._send(502, {"error": str(e)})
        except RuntimeError as e:
            self._send(502, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            body = self._read_json()
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            a1_prefix = f"/A1-P/v2/policytypes/{POLICY_TYPE_ID}/policies/"
            a1_prefix_l = f"/a1-p/v2/policytypes/{POLICY_TYPE_ID}/policies/"
            if path.startswith(a1_prefix) or path.startswith(a1_prefix_l):
                prefix = a1_prefix if path.startswith(a1_prefix) else a1_prefix_l
                pid = unquote(path[len(prefix) :])
                if not pid or "/" in pid:
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, self._ctrl().put_a1_instance(pid, body))
                return
            if path.startswith("/api/v1/policies/") or path.startswith("/api/v1/intents/"):
                prefix = "/api/v1/policies/" if path.startswith("/api/v1/policies/") else "/api/v1/intents/"
                pid = unquote(path[len(prefix) :])
                if not pid or "/" in pid:
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, self._ctrl().update_policy(pid, body))
                return
            self._send(404, {"error": "not found"})
        except KeyError:
            self._send(404, {"error": "policy not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path.startswith("/api/v1/a1/policies/"):
                pid = unquote(path[len("/api/v1/a1/policies/") :])
                if not pid or "/" in pid:
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, self._ctrl().delete_active_policy(pid))
                return
            if path.startswith("/api/v1/policies/") or path.startswith("/api/v1/intents/"):
                prefix = "/api/v1/policies/" if path.startswith("/api/v1/policies/") else "/api/v1/intents/"
                pid = unquote(path[len(prefix) :])
                if not self._ctrl().store.delete_policy(pid):
                    self._send(404, {"error": f"policy {pid} not found"})
                    return
                self._send(200, {"ok": True, "deleted": pid})
                return
            self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except ConnectionError as e:
            self._send(502, {"error": str(e)})
        except RuntimeError as e:
            self._send(502, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def start_api(host: str, port: int, controller: RappController) -> ThreadingHTTPServer:
    RappApiHandler.controller = controller
    httpd = ThreadingHTTPServer((host, port), RappApiHandler)
    t = threading.Thread(target=httpd.serve_forever, name="rapp-api", daemon=True)
    t.start()
    print(f"rApp REST API listening on http://{host}:{port}", flush=True)
    print(f"  A1 type     {POLICY_TYPE_ID}", flush=True)
    print(f"  Swagger UI  http://{host}:{port}/docs", flush=True)
    print(f"  Console     http://{host}:{DEFAULT_UI_PORT}/", flush=True)
    print(f"  xApp base   {controller.xapp.base}", flush=True)
    if controller.a1_pms:
        print(f"  A1 PMS base {controller.a1_pms.base} (ric: {controller.a1_pms.ric_id}, type: {controller.a1_pms.type_id})", flush=True)
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser(description="Non-RT O-RAN A1 Slice SLA rApp")
    ap.add_argument("--api-host", default=DEFAULT_API_HOST)
    ap.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    ap.add_argument("--xapp", default=DEFAULT_XAPP, help="near-RT xApp API base URL")
    ap.add_argument("--a1-pms", default=DEFAULT_A1_PMS, help="Non-RT RIC A1 Policy Management Service base URL")
    ap.add_argument("--a1-ric", default=DEFAULT_A1_RIC, help="Near-RT RIC identifier configured in A1 PMS")
    ap.add_argument("--a1-type", default=DEFAULT_A1_TYPE_ID, help="A1 policy type ID (e.g. 20008)")
    ap.add_argument("--store", type=Path, default=STORE_PATH)
    args = ap.parse_args()

    store = Store(args.store.expanduser().resolve())
    a1_pms = A1PmsClient(args.a1_pms, ric_id=args.a1_ric, type_id=args.a1_type) if args.a1_pms else None
    controller = RappController(store, XappClient(args.xapp), a1_pms=a1_pms)
    httpd = start_api(args.api_host, args.api_port, controller)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping rApp backend...", flush=True)
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

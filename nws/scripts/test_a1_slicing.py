#!/usr/bin/env python3
"""
test_a1_slicing.py — Automated End-to-End Testing Script for O-RAN A1 Network Slicing.

Tests standard operational A1 slicing APIs across Non-RT RIC rApp and Near-RT RIC xApp:
  1. Service health & A1 PMS connectivity (GET /health, GET /api/v1/status)
  2. Single-slice SLA policy lifecycle & E2 PRB translation (POST /api/v1/activate -> GET /api/v1/results)
  3. Dynamic SLA reconfiguration & PRB quota recalculation
  4. Multi-slice isolation and total PRB capacity constraints
  5. Input validation & negative schema boundary checks (POST /api/v1/policies/preview)
  6. Operational Results API contract verification (GET /api/v1/results)

Usage:
  python3 scripts/test_a1_slicing.py
  python3 scripts/test_a1_slicing.py --json
  python3 scripts/test_a1_slicing.py --case lifecycle
  python3 scripts/test_a1_slicing.py --rapp-url http://10.1.101.18:31090 --xapp-url http://10.1.101.18:31080
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class A1SlicingTester:
    def __init__(self, rapp_url: str, xapp_url: str, timeout: float = 15.0, verbose: bool = False) -> None:
        self.rapp_url = rapp_url.rstrip("/")
        self.xapp_url = xapp_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self.results: list[dict[str, Any]] = []

    def _http(self, url: str, method: str = "GET", body: Any = None, expect_status: int = 200) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        t0 = time.time()
        try:
            with urlopen(req, timeout=10.0) as resp:
                elapsed = time.time() - t0
                raw = resp.read().decode("utf-8", errors="ignore")
                parsed = json.loads(raw) if raw.strip() else {}
                if self.verbose:
                    print(f"  -> {method} {url} [{resp.status}] in {elapsed:.3f}s")
                return int(resp.status), parsed
        except HTTPError as e:
            elapsed = time.time() - t0
            raw = e.read().decode("utf-8", errors="ignore") if e.fp else ""
            parsed = {}
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = {"raw": raw}
            if self.verbose:
                print(f"  -> {method} {url} [{e.code}] in {elapsed:.3f}s")
            return int(e.code), parsed
        except (URLError, TimeoutError, OSError) as e:
            elapsed = time.time() - t0
            raise ConnectionError(f"HTTP request to {url} failed: {e}") from e

    def _record(self, name: str, passed: bool, duration_sec: float, details: dict[str, Any], error: Optional[str] = None) -> None:
        rec = {
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "duration_sec": round(duration_sec, 3),
            "details": details,
        }
        if error:
            rec["error"] = error
        self.results.append(rec)

        tag = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
        print(f"{tag} {BOLD}{name}{RESET} ({rec['duration_sec']}s)")
        if error:
            print(f"       {RED}Error: {error}{RESET}")
        elif self.verbose and details:
            for k, v in details.items():
                print(f"       {CYAN}{k}:{RESET} {v}")

    # =========================================================================
    # Test Cases
    # =========================================================================

    def test_health_and_connectivity(self) -> bool:
        t0 = time.time()
        details: dict[str, Any] = {}
        try:
            # 1. rApp health
            code_r, body_r = self._http(f"{self.rapp_url}/health")
            if code_r != 200:
                raise RuntimeError(f"rApp health HTTP {code_r}: {body_r}")
            details["rapp_health"] = "200 OK"

            # 2. xApp health
            code_x, body_x = self._http(f"{self.xapp_url}/health")
            if code_x != 200:
                raise RuntimeError(f"xApp health HTTP {code_x}: {body_x}")
            details["xapp_health"] = "200 OK"

            # 3. rApp status & PMS reachability
            code_s, body_s = self._http(f"{self.rapp_url}/api/v1/status")
            if code_s != 200:
                raise RuntimeError(f"rApp status HTTP {code_s}: {body_s}")
            pms = body_s.get("a1_pms", {})
            if not pms.get("ok"):
                raise RuntimeError(f"A1 PMS connection error: {pms.get('error')}")
            details["pms_ric_id"] = pms.get("ric_id")
            details["pms_policy_type"] = pms.get("type_id")

            self._record("1_health_and_connectivity", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("1_health_and_connectivity", False, time.time() - t0, details, str(e))
            return False

    def test_single_slice_lifecycle(self) -> bool:
        """Activate eMBB slice SLA -> wait for sync -> verify in /api/v1/results."""
        t0 = time.time()
        details: dict[str, Any] = {}
        sst = 1
        sd = "000101"
        try:
            policy = {
                "scope": {
                    "sliceId": {
                        "sst": sst,
                        "sd": sd,
                        "plmnId": {"mcc": "001", "mnc": "01"},
                    }
                },
                "sliceSlaObjectives": {
                    "guaDlThptPerSlice": 20000,
                    "maxDlThptPerSlice": 100000,
                    "maxNumberOfUes": 100,
                },
            }

            # 1. Activate via rApp standard endpoint
            t_act = time.time()
            code, resp = self._http(f"{self.rapp_url}/api/v1/activate", method="POST", body=policy)
            if code != 200 or not resp.get("ok"):
                raise RuntimeError(f"Activation failed HTTP {code}: {resp}")
            details["activated_policy_id"] = resp.get("policy_id")

            # 2. Poll /api/v1/results until policy is synchronized to xApp and E2 PRB is set
            synced = False
            prb_info: dict[str, Any] = {}
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                r_code, r_body = self._http(f"{self.rapp_url}/api/v1/results")
                if r_code == 200 and isinstance(r_body, dict):
                    for s in r_body.get("slices", []):
                        if s.get("sst") == sst and str(s.get("sd", "")).lower().endswith(sd.lower()):
                            # 20,000 kbps maps to 10.0% dedicated PRB
                            if s.get("dedicated_prb_pct") == 10.0:
                                synced = True
                                prb_info = s
                                break
                if synced:
                    break
                time.sleep(0.5)

            if not synced:
                raise TimeoutError(f"Slice SST={sst} SD={sd} did not reach expected 10% PRB within {self.timeout}s")

            sync_latency = round(time.time() - t_act, 3)
            details["sync_latency_sec"] = sync_latency
            details["dedicated_prb_pct"] = prb_info.get("dedicated_prb_pct")
            details["max_prb_pct"] = prb_info.get("max_prb_pct")
            details["has_a1_sla"] = prb_info.get("has_a1_sla")

            self._record("2_single_slice_lifecycle", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("2_single_slice_lifecycle", False, time.time() - t0, details, str(e))
            return False

    def test_dynamic_sla_reconfiguration(self) -> bool:
        """Update existing slice SLA throughput -> verify PRB dedicated % updates dynamically."""
        t0 = time.time()
        details: dict[str, Any] = {}
        sst = 1
        sd = "000101"
        try:
            # Upgrade SLA: Gua DL = 30,000 kbps (maps to 15.0% dedicated PRBs)
            updated_policy = {
                "scope": {
                    "sliceId": {
                        "sst": sst,
                        "sd": sd,
                        "plmnId": {"mcc": "001", "mnc": "01"},
                    }
                },
                "sliceSlaObjectives": {
                    "guaDlThptPerSlice": 30000,
                    "maxDlThptPerSlice": 100000,
                    "maxNumberOfUes": 150,
                },
            }

            t_upd = time.time()
            code, resp = self._http(f"{self.rapp_url}/api/v1/activate", method="POST", body=updated_policy)
            if code != 200 or not resp.get("ok"):
                raise RuntimeError(f"Reconfiguration failed HTTP {code}: {resp}")

            # Poll /api/v1/results until dedicated PRB updates to 15%
            updated = False
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                r_code, r_body = self._http(f"{self.rapp_url}/api/v1/results")
                if r_code == 200 and isinstance(r_body, dict):
                    for s in r_body.get("slices", []):
                        if s.get("sst") == sst and str(s.get("sd", "")).lower().endswith(sd.lower()):
                            if s.get("dedicated_prb_pct") == 15.0:
                                updated = True
                                details["updated_dedicated_prb_pct"] = 15.0
                                details["updated_max_prb_pct"] = s.get("max_prb_pct")
                                break
                if updated:
                    break
                time.sleep(0.5)

            if not updated:
                raise TimeoutError(f"Slice SST={sst} SD={sd} did not dynamically update to 15% PRB within {self.timeout}s")

            details["reconfig_latency_sec"] = round(time.time() - t_upd, 3)
            self._record("3_dynamic_sla_reconfiguration", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("3_dynamic_sla_reconfiguration", False, time.time() - t0, details, str(e))
            return False

    def test_multi_slice_concurrency(self) -> bool:
        """Activate a second slice (URLLC) -> verify multi-slice isolation and PRB share sum."""
        t0 = time.time()
        details: dict[str, Any] = {}
        sst = 1
        sd2 = "000102"
        try:
            urllc_policy = {
                "scope": {
                    "sliceId": {
                        "sst": sst,
                        "sd": sd2,
                        "plmnId": {"mcc": "001", "mnc": "01"},
                    }
                },
                "sliceSlaObjectives": {
                    "guaDlThptPerSlice": 10000,  # 10,000 kbps maps to 5.0% PRB
                    "maxDlThptPerSlice": 50000,
                    "maxDlPacketDelayPerUe": 10.0,
                    "maxDlPdcpSduPacketLossRatePerUe": 0.01,
                },
            }

            code, resp = self._http(f"{self.rapp_url}/api/v1/activate", method="POST", body=urllc_policy)
            if code != 200 or not resp.get("ok"):
                raise RuntimeError(f"URLLC slice activation failed: {resp}")

            # Verify both slices appear in results
            coexists = False
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                r_code, r_body = self._http(f"{self.rapp_url}/api/v1/results")
                if r_code == 200 and isinstance(r_body, dict):
                    slices = r_body.get("slices", [])
                    has_s1 = any(str(s.get("sd", "")).lower().endswith("000101") for s in slices)
                    has_s2 = any(str(s.get("sd", "")).lower().endswith(sd2.lower()) for s in slices)
                    if has_s1 and has_s2:
                        coexists = True
                        summary = r_body.get("summary", {})
                        details["total_slices"] = summary.get("total_slices")
                        details["total_dedicated_prb_pct"] = summary.get("total_dedicated_prb_pct")
                        details["shared_prb_pool_pct"] = summary.get("shared_prb_pool_pct")
                        # Validate PRB constraints: dedicated sum must be <= 100%
                        if summary.get("total_dedicated_prb_pct", 0) > 100.0:
                            raise ValueError("Total dedicated PRB exceeded 100% capacity limit")
                        break
                time.sleep(0.5)

            if not coexists:
                raise TimeoutError("Multi-slice coexistence could not be verified in time")

            self._record("4_multi_slice_concurrency", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("4_multi_slice_concurrency", False, time.time() - t0, details, str(e))
            return False

    def test_negative_schema_validation(self) -> bool:
        """Verify schema rejection on malformed / out-of-range inputs."""
        t0 = time.time()
        details: dict[str, Any] = {}
        try:
            # 1. Invalid SST (out of range: 999 > 255)
            invalid_sst = {
                "scope": {"sliceId": {"sst": 999, "plmnId": {"mcc": "001", "mnc": "01"}}},
                "sliceSlaObjectives": {"guaDlThptPerSlice": 10000},
            }
            c1, r1 = self._http(f"{self.rapp_url}/api/v1/policies/preview", method="POST", body=invalid_sst)
            if c1 == 200:
                raise ValueError("Expected failure on SST=999 but got HTTP 200")
            details["invalid_sst_status"] = c1

            # 2. Invalid SD (non-hex characters)
            invalid_sd = {
                "scope": {"sliceId": {"sst": 1, "sd": "ZZZZZZ", "plmnId": {"mcc": "001", "mnc": "01"}}},
                "sliceSlaObjectives": {"guaDlThptPerSlice": 10000},
            }
            c2, r2 = self._http(f"{self.rapp_url}/api/v1/policies/preview", method="POST", body=invalid_sd)
            if c2 == 200:
                raise ValueError("Expected failure on invalid SD 'ZZZZZZ' but got HTTP 200")
            details["invalid_sd_status"] = c2

            self._record("5_negative_schema_validation", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("5_negative_schema_validation", False, time.time() - t0, details, str(e))
            return False

    def test_results_api_contract(self) -> bool:
        """Verify GET /api/v1/results response format and contract."""
        t0 = time.time()
        details: dict[str, Any] = {}
        try:
            # 1. Test rApp /api/v1/results
            c_r, body_r = self._http(f"{self.rapp_url}/api/v1/results")
            if c_r != 200:
                raise RuntimeError(f"rApp /api/v1/results returned HTTP {c_r}")
            for req_key in ("status", "summary", "slices", "a1_policies", "timestamp"):
                if req_key not in body_r:
                    raise KeyError(f"Missing required key '{req_key}' in rApp /api/v1/results")
            details["rapp_results_slices"] = len(body_r.get("slices", []))

            # 2. Test xApp /api/v1/results
            c_x, body_x = self._http(f"{self.xapp_url}/api/v1/results")
            if c_x != 200:
                raise RuntimeError(f"xApp /api/v1/results returned HTTP {c_x}")
            for req_key in ("status", "summary", "slices", "a1_policies", "timestamp"):
                if req_key not in body_x:
                    raise KeyError(f"Missing required key '{req_key}' in xApp /api/v1/results")
            details["xapp_results_slices"] = len(body_x.get("slices", []))

            self._record("6_results_api_contract", True, time.time() - t0, details)
            return True
        except Exception as e:
            self._record("6_results_api_contract", False, time.time() - t0, details, str(e))
            return False

    def run_all(self, case: str = "all") -> dict[str, Any]:
        print(f"\n{BOLD}=== Starting O-RAN A1 Network Slicing Test Suite ==={RESET}")
        print(f"rApp Target: {CYAN}{self.rapp_url}{RESET}")
        print(f"xApp Target: {CYAN}{self.xapp_url}{RESET}\n")

        t_start = time.time()
        cases = {
            "health": self.test_health_and_connectivity,
            "lifecycle": self.test_single_slice_lifecycle,
            "update": self.test_dynamic_sla_reconfiguration,
            "multi": self.test_multi_slice_concurrency,
            "negative": self.test_negative_schema_validation,
            "results": self.test_results_api_contract,
        }

        if case == "all":
            for fn in cases.values():
                fn()
        elif case in cases:
            cases[case]()
        else:
            print(f"{RED}Unknown test case: {case}. Available: {list(cases.keys()) + ['all']}{RESET}")
            sys.exit(1)

        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed
        total_duration = round(time.time() - t_start, 3)

        summary = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_duration_sec": total_duration,
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": failed,
            "all_passed": failed == 0,
            "tests": self.results,
        }

        print(f"\n{BOLD}=== Test Execution Summary ==={RESET}")
        print(f"Total: {total} | Passed: {GREEN}{passed}{RESET} | Failed: {RED if failed else GREEN}{failed}{RESET} | Duration: {total_duration}s")
        if failed == 0:
            print(f"{GREEN}{BOLD}✓ ALL A1 NETWORK SLICING TESTS PASSED!{RESET}\n")
        else:
            print(f"{RED}{BOLD}✗ SOME TESTS FAILED!{RESET}\n")

        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Test A1 Network Slicing end-to-end via standard operational APIs.")
    parser.add_argument("--rapp-url", default="http://localhost:31090", help="Non-RT RIC rApp API base URL (default: http://localhost:31090)")
    parser.add_argument("--xapp-url", default="http://localhost:31080", help="Near-RT RIC xApp API base URL (default: http://localhost:31080)")
    parser.add_argument("--case", default="all", choices=["all", "health", "lifecycle", "update", "multi", "negative", "results"], help="Test case to run")
    parser.add_argument("--timeout", type=float, default=15.0, help="Max wait seconds for policy sync (default: 15)")
    parser.add_argument("--json", action="store_true", help="Print structured JSON report instead of text")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed request and response steps")
    args = parser.parse_args()

    tester = A1SlicingTester(
        rapp_url=args.rapp_url,
        xapp_url=args.xapp_url,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    report = tester.run_all(case=args.case)

    if args.json:
        print(json.dumps(report, indent=2))

    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()

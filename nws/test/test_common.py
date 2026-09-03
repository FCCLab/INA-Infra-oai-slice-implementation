#!/usr/bin/env python3
"""
Shared 3GPP Network Slicing live rfsim test library (PR #451).

Two runners import this module:

  test.py            — apply slice dedicated/min/max via gNB startup YAML
                       (`bringup.py --scenario`). Restart gNB/UEs at the start
                       of a run, then again only when the YAML slice group changes.
  test_with_e2ap.py  — restart gNB/UEs once per run at NSBOTH 0/0/100%; later
                       test cases only change slice policy via FlexRIC xApp E2.
                       Traffic starts after mac stats show the new dedicated/min/max.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import telnetlib
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR.parent / "scripts"
BRINGUP_PY = SCRIPTS_DIR / "bringup.py"
CORE_COMPOSE = BASE_DIR.parent / "5gc" / "open5gs" / "docker-compose.yml"
XAPP_COMPOSE = SCRIPTS_DIR / "xapp" / "docker-compose.yml"

XAPP_API_URL = "http://127.0.0.1:18080/api/v1/slices"
XAPP_HEALTH_URL = "http://127.0.0.1:18080/health"
CORE_CONTAINER = "nws-5gc"
GNB_CONTAINER = "nws-oai-gnb"
RIC_CONTAINER = "nws-nearRT-RIC"
XAPP_CONTAINER = "nws-xapp-slice-monitor"
UPF_IP = "10.45.0.1"

# How slice dedicated/min/max is pushed into the gNB.
SLICE_CONFIG_STARTUP = "startup"  # bringup.py --scenario patches gNB YAML
SLICE_CONFIG_E2AP = "e2ap"        # FlexRIC xApp PUT /api/v1/slices (E2 Slice SM)
SLICE_CONFIG_LABELS = {
    SLICE_CONFIG_STARTUP: "gNB startup YAML (bringup --scenario)",
    SLICE_CONFIG_E2AP: "FlexRIC xApp E2AP Slice SM REST",
}

from tmux_manager import TMUX_SESSION, TmuxManager


@dataclass
class SliceRule:
    sst: int
    sd: str
    direction: str  # "dl", "ul", or "both"
    dedicated: float
    min_ratio: float
    max_ratio: float


@dataclass
class TestScenario:
    name: str
    title: str
    description: str
    active_ues: list[int]
    idle_ues: list[int]
    rules: list[SliceRule]
    explanation: str
    expected_shares: dict[int, tuple[float, float]]  # ue_idx -> (min_pct, max_pct)
    test_mode: str = "iperf"  # "iperf" or "ping"
    direction: str = "ul"     # "ul" or "dl"
    protocol: str = "udp"     # "udp" or "tcp"


_AS_NO_SLICE_RULES = [
    SliceRule(1, "0x000001", "both", 0.0, 0.0, 100.0),
    SliceRule(1, "0x000002", "both", 0.0, 0.0, 100.0),
    SliceRule(1, "0x000003", "both", 0.0, 0.0, 100.0),
    SliceRule(1, "0x000004", "both", 0.0, 0.0, 100.0),
    SliceRule(1, "0x000005", "both", 0.0, 0.0, 100.0),
]

_AS_NO_SLICE_RAN = (
    "RAN bringup: NSBOTH (DL+UL SCHE_NS) with extra slice allocator pass per slot. "
    "xApp policy: dedicated=0%, min=0%, max=100% on all five S-NSSAIs (as-no-slice caps, not PF)."
)

SCENARIOS: dict[str, TestScenario] = {
    # 000. True Pure Proportional Fair Policy (sch=PF, DL=PF UL=PF, no slicing algorithm)
    "pf_only": TestScenario(
        name="pf_only",
        title="NO SLICE PF PING (True Proportional Fair, sch=PF, 30s 5-UE Ping)",
        description="Pure OAI Proportional Fair scheduler (sch=PF, DL=PF, UL=PF). Slicing algorithm completely bypassed. 30s ping across all 5 UEs.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[],
        explanation="Standard OAI Proportional Fair scheduler without slicing passes. All 5 UEs ping UPF Core (10.45.0.1) with <= 5% loss and < 50ms latency.",
        expected_shares={},
        test_mode="ping",
    ),
    "pf_dl_udp": TestScenario(
        name="pf_dl_udp",
        title="NO SLICE PF DL UDP (True Proportional Fair Downlink UDP)",
        description="Pure OAI Proportional Fair scheduler (sch=PF). All 5 UEs active under full competitive Downlink UDP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[],
        explanation="Pure OAI Proportional Fair scheduler balances DL traffic evenly across all 5 active UEs (~12-28% share each).",
        expected_shares={
            1: (12.0, 28.0),
            2: (12.0, 28.0),
            3: (12.0, 28.0),
            4: (12.0, 28.0),
            5: (12.0, 28.0),
        },
        test_mode="iperf",
        direction="dl",
        protocol="udp",
    ),
    "pf_dl_tcp": TestScenario(
        name="pf_dl_tcp",
        title="NO SLICE PF DL TCP (True Proportional Fair Downlink TCP)",
        description="Pure OAI Proportional Fair scheduler (sch=PF). All 5 UEs active under full competitive Downlink TCP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[],
        explanation="Pure OAI Proportional Fair scheduler balances DL TCP streams evenly across all 5 active UEs (~12-32% share each).",
        expected_shares={
            1: (12.0, 32.0),
            2: (12.0, 32.0),
            3: (12.0, 32.0),
            4: (12.0, 32.0),
            5: (12.0, 32.0),
        },
        test_mode="iperf",
        direction="dl",
        protocol="tcp",
    ),
    "pf_ul_udp": TestScenario(
        name="pf_ul_udp",
        title="NO SLICE PF UL UDP (True Proportional Fair Uplink UDP)",
        description="Pure OAI Proportional Fair scheduler (sch=PF). All 5 UEs active under full competitive Uplink UDP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[],
        explanation="Pure OAI Proportional Fair scheduler balances UL traffic evenly across all 5 active UEs (~12-28% share each).",
        expected_shares={
            1: (12.0, 28.0),
            2: (12.0, 28.0),
            3: (12.0, 28.0),
            4: (12.0, 28.0),
            5: (12.0, 28.0),
        },
        test_mode="iperf",
        direction="ul",
        protocol="udp",
    ),
    "pf_ul_tcp": TestScenario(
        name="pf_ul_tcp",
        title="NO SLICE PF UL TCP (True Proportional Fair Uplink TCP)",
        description="Pure OAI Proportional Fair scheduler (sch=PF). All 5 UEs active under full competitive Uplink TCP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[],
        explanation="Pure OAI Proportional Fair scheduler balances UL TCP streams evenly across all 5 active UEs (~12-32% share each).",
        expected_shares={
            1: (12.0, 32.0),
            2: (12.0, 32.0),
            3: (12.0, 32.0),
            4: (12.0, 32.0),
            5: (12.0, 32.0),
        },
        test_mode="iperf",
        direction="ul",
        protocol="tcp",
    ),

    # 0. As-no-slice policy (0/0/100%) under NSBOTH — not true PF (use bringup --sch PF for that)
    "as_no_slice": TestScenario(
        name="as_no_slice",
        title="AS-NO-SLICE PING (0/0/100% under NSBOTH, 30s 5-UE Ping)",
        description=f"{_AS_NO_SLICE_RAN} 30s ping across all 5 UEs.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Fair-share intent without dedicated/min caps; slice PRB windows still computed each slot. UPF ping <= 5% loss, < 50ms latency.",
        expected_shares={},
        test_mode="ping",
    ),
    "as_no_slice_dl_udp": TestScenario(
        name="as_no_slice_dl_udp",
        title="AS-NO-SLICE DL UDP (0/0/100% under NSBOTH, equal fair share)",
        description=f"{_AS_NO_SLICE_RAN} All 5 UEs, full competitive DL UDP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Intra-slice proportional fair under NS; no PRB min/max isolation. Expect ~12-28% DL share per UE.",
        expected_shares={
            1: (12.0, 28.0),
            2: (12.0, 28.0),
            3: (12.0, 28.0),
            4: (12.0, 28.0),
            5: (12.0, 28.0),
        },
        test_mode="iperf",
        direction="dl",
        protocol="udp",
    ),
    "as_no_slice_dl_tcp": TestScenario(
        name="as_no_slice_dl_tcp",
        title="AS-NO-SLICE DL TCP (0/0/100% under NSBOTH, equal fair share)",
        description=f"{_AS_NO_SLICE_RAN} All 5 UEs, full competitive DL TCP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Intra-slice proportional fair under NS; no PRB min/max isolation. Expect ~12-32% DL share per UE.",
        expected_shares={
            1: (12.0, 32.0),
            2: (12.0, 32.0),
            3: (12.0, 32.0),
            4: (12.0, 32.0),
            5: (12.0, 32.0),
        },
        test_mode="iperf",
        direction="dl",
        protocol="tcp",
    ),
    "as_no_slice_ul_udp": TestScenario(
        name="as_no_slice_ul_udp",
        title="AS-NO-SLICE UL UDP (0/0/100% under NSBOTH, equal fair share)",
        description=f"{_AS_NO_SLICE_RAN} All 5 UEs, full competitive UL UDP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Intra-slice proportional fair under NS; no PRB min/max isolation. Expect ~12-28% UL share per UE.",
        expected_shares={
            1: (12.0, 28.0),
            2: (12.0, 28.0),
            3: (12.0, 28.0),
            4: (12.0, 28.0),
            5: (12.0, 28.0),
        },
        test_mode="iperf",
        direction="ul",
        protocol="udp",
    ),
    "as_no_slice_ul_tcp": TestScenario(
        name="as_no_slice_ul_tcp",
        title="AS-NO-SLICE UL TCP (0/0/100% under NSBOTH, equal fair share)",
        description=f"{_AS_NO_SLICE_RAN} All 5 UEs, full competitive UL TCP load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Intra-slice proportional fair under NS; no PRB min/max isolation. Expect ~12-32% UL share per UE.",
        expected_shares={
            1: (12.0, 32.0),
            2: (12.0, 32.0),
            3: (12.0, 32.0),
            4: (12.0, 32.0),
            5: (12.0, 32.0),
        },
        test_mode="iperf",
        direction="ul",
        protocol="tcp",
    ),
    "as_no_slice_full": TestScenario(
        name="as_no_slice_full",
        title="AS-NO-SLICE FULL (0/0/100% under NSBOTH, equal fair share)",
        description=f"{_AS_NO_SLICE_RAN} All 5 UEs under full competitive load.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=list(_AS_NO_SLICE_RULES),
        explanation="Intra-slice proportional fair under NS; no PRB min/max isolation. Expect ~12-28% share per UE.",
        expected_shares={
            1: (12.0, 28.0),
            2: (12.0, 28.0),
            3: (12.0, 28.0),
            4: (12.0, 28.0),
            5: (12.0, 28.0),
        },
        test_mode="iperf",
        direction="ul",
        protocol="udp",
    ),

    # 1. Dedicated Policy (Pass 1)
    "dedicated_sym_idle": TestScenario(
        name="dedicated_sym_idle",
        title="DEDICATED SYMMETRIC IDLE (15/15/15/15/15% Idle Isolation)",
        description="UE1 full traffic; UEs 2-5 IDLE. Slices 2-5 dedicated PRBs (4x15%=60%) remain strictly RESERVED.",
        active_ues=[1],
        idle_ues=[2, 3, 4, 5],
        rules=[
            SliceRule(1, "0x000001", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000002", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000003", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000004", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000005", "both", 15.0, 15.0, 100.0),
        ],
        explanation="Slices 2-5 are idle, but their dedicated bandwidth is held and NOT surrendered. UE1 is restricted to its own slice + unreserved PRBs.",
        expected_shares={1: (60.0, 100.0)},
    ),
    "dedicated_sym_full": TestScenario(
        name="dedicated_sym_full",
        title="DEDICATED SYMMETRIC FULL (15/15/15/15/15% Equal Load)",
        description="Equal 15% dedicated per slice (~20 PRBs each on 133 PRB carrier).",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000002", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000003", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000004", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000005", "both", 15.0, 15.0, 100.0),
        ],
        explanation="Under load, all 5 UEs receive similar goodput (~20-24 Mbps DL each) in equal 15% dedicated windows.",
        expected_shares={
            1: (15.0, 25.0),
            2: (15.0, 25.0),
            3: (15.0, 25.0),
            4: (15.0, 25.0),
            5: (15.0, 25.0),
        },
    ),
    "dedicated_asym_idle": TestScenario(
        name="dedicated_asym_idle",
        title="DEDICATED ASYMMETRIC IDLE (15/15/15/15/7% Idle Isolation)",
        description="UE1 full traffic; UEs 2-5 IDLE. Slices 2-5 dedicated PRBs (52%) remain strictly RESERVED.",
        active_ues=[1],
        idle_ues=[2, 3, 4, 5],
        rules=[
            SliceRule(1, "0x000001", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000002", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000003", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000004", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000005", "both", 7.0, 7.0, 100.0),
        ],
        explanation="Slices 2-5 are idle, but hold 52% dedicated PRBs without leaking to active Slice 1.",
        expected_shares={1: (60.0, 100.0)},
    ),
    "dedicated_asym_full": TestScenario(
        name="dedicated_asym_full",
        title="DEDICATED ASYMMETRIC FULL (15/15/15/15/7% Split)",
        description="Slices 1-4 get 15% dedicated (~20 PRBs); Slice 5 gets 7% dedicated (~9 PRBs).",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000002", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000003", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000004", "both", 15.0, 15.0, 100.0),
            SliceRule(1, "0x000005", "both", 7.0, 7.0, 100.0),
        ],
        explanation="Under load, Slices 1-4 each receive ~20-25% share, while Slice 5 receives ~8-14% reflecting its smaller 7% window.",
        expected_shares={
            1: (18.0, 26.0),
            2: (18.0, 26.0),
            3: (18.0, 26.0),
            4: (18.0, 26.0),
            5: (7.0, 14.0),
        },
    ),

    # 2. Min Policy (Pass 2)
    "min_sym_idle": TestScenario(
        name="min_sym_idle",
        title="MIN SYMMETRIC IDLE (20/20/20/20/20% Shareable Burst)",
        description="UE1 full traffic; UEs 2-5 IDLE. Unused min PRBs from idle slices are surrendered to UE1.",
        active_ues=[1],
        idle_ues=[2, 3, 4, 5],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 20.0, 100.0),
        ],
        explanation="Unlike dedicated resources, Min resources are shared when idle. UE1 bursts to 100% full capacity!",
        expected_shares={1: (85.0, 100.0)},
    ),
    "min_sym_full": TestScenario(
        name="min_sym_full",
        title="MIN SYMMETRIC FULL (20/20/20/20/20% Equal Min)",
        description="Equal 20% minimum per slice (~27 PRBs each) with dedicated=0, max=100%.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 20.0, 100.0),
        ],
        explanation="Under full load, all 5 UEs achieve similar goodput (~26-29 Mbps DL each) matching equal 20% min guarantees.",
        expected_shares={
            1: (15.0, 25.0),
            2: (15.0, 25.0),
            3: (15.0, 25.0),
            4: (15.0, 25.0),
            5: (15.0, 25.0),
        },
    ),
    "min_asym_idle": TestScenario(
        name="min_asym_idle",
        title="MIN ASYMMETRIC IDLE (20/20/20/20/10% Shareable Burst)",
        description="UE1 full traffic; UEs 2-5 IDLE. Unused min PRBs are surrendered to UE1.",
        active_ues=[1],
        idle_ues=[2, 3, 4, 5],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 10.0, 100.0),
        ],
        explanation="Unused min PRBs are released. Active UE1 bursts to full capacity!",
        expected_shares={1: (85.0, 100.0)},
    ),
    "min_asym_full": TestScenario(
        name="min_asym_full",
        title="MIN ASYMMETRIC FULL (20/20/20/20/10% Split)",
        description="Slices 1-4 get 20% min (~27 PRBs); Slice 5 gets 10% min (~13 PRBs).",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 20.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 10.0, 100.0),
        ],
        explanation="Under load, UE5 gets roughly half the goodput of UEs 1-4 (~13 vs ~27 Mbps DL) in exact accordance with min ratios.",
        expected_shares={
            1: (15.0, 28.0),
            2: (15.0, 28.0),
            3: (15.0, 28.0),
            4: (15.0, 28.0),
            5: (7.0, 16.0),
        },
    ),

    # 3. Max Policy (Pass 3)
    "max_sym_idle": TestScenario(
        name="max_sym_idle",
        title="MAX SYMMETRIC IDLE (100/100/100/100/100% Uncapped Burst)",
        description="UE1 full traffic; UEs 2-5 IDLE. UE1 bursts freely across entire cell bandwidth.",
        active_ues=[1],
        idle_ues=[2, 3, 4, 5],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 0.0, 100.0),
        ],
        explanation="No ceiling cap. UE1 bursts freely up to 100% of cell bandwidth.",
        expected_shares={1: (85.0, 100.0)},
    ),
    "max_sym_full": TestScenario(
        name="max_sym_full",
        title="MAX SYMMETRIC FULL (100/100/100/100/100% Fair Share)",
        description="No ceiling cap. All 5 slices compete freely for full 133 PRBs.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000005", "both", 0.0, 0.0, 100.0),
        ],
        explanation="Under contention, the proportional fair scheduler shares the entire cell capacity fairly (~27-29 Mbps DL each).",
        expected_shares={
            1: (15.0, 25.0),
            2: (15.0, 25.0),
            3: (15.0, 25.0),
            4: (15.0, 25.0),
            5: (15.0, 25.0),
        },
    ),
    "max_asym_idle": TestScenario(
        name="max_asym_idle",
        title="MAX ASYMMETRIC IDLE (100/100/100/50/50% Capped Ceiling)",
        description="UE5 full traffic (50% max); UEs 1-4 IDLE. UE5 is strictly capped at 50% capacity despite idle channel.",
        active_ues=[5],
        idle_ues=[1, 2, 3, 4],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 0.0, 50.0),
            SliceRule(1, "0x000005", "both", 0.0, 0.0, 50.0),
        ],
        explanation="Even though the channel is idle, Slice 5 cannot exceed its 50% max ceiling cap (~13 Mbps max).",
        expected_shares={5: (85.0, 100.0)},
    ),
    "max_asym_full": TestScenario(
        name="max_asym_full",
        title="MAX ASYMMETRIC FULL (100/100/100/50/50% Capped Contention)",
        description="Slices 1-3 uncapped (100%); Slices 4-5 strictly capped at 50% ceiling.",
        active_ues=[1, 2, 3, 4, 5],
        idle_ues=[],
        rules=[
            SliceRule(1, "0x000001", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000002", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000003", "both", 0.0, 0.0, 100.0),
            SliceRule(1, "0x000004", "both", 0.0, 0.0, 50.0),
            SliceRule(1, "0x000005", "both", 0.0, 0.0, 50.0),
        ],
        explanation="Slices 1-3 reach ~27-29 Mbps DL, while Slices 4-5 are throttled at ~13 Mbps DL by the 50% max cap.",
        expected_shares={
            1: (22.0, 32.0),
            2: (22.0, 32.0),
            3: (22.0, 32.0),
            4: (10.0, 17.0),
            5: (10.0, 17.0),
        },
    ),
}

# Auto-expand all scenarios into DL UDP, DL TCP, UL UDP, UL TCP variants
TRAFFIC_MODES = [
    ("dl_udp", "dl", "udp", "Downlink UDP"),
    ("dl_tcp", "dl", "tcp", "Downlink TCP"),
    ("ul_udp", "ul", "udp", "Uplink UDP"),
    ("ul_tcp", "ul", "tcp", "Uplink TCP"),
]

for _base_key in list(SCENARIOS.keys()):
    _base_sc = SCENARIOS[_base_key]
    if _base_sc.test_mode == "ping":
        continue
    for _sfx, _d, _p, _lbl in TRAFFIC_MODES:
        _var_key = f"{_base_key}_{_sfx}" if not _base_key.endswith(f"_{_sfx}") else _base_key
        if _var_key not in SCENARIOS:
            SCENARIOS[_var_key] = TestScenario(
                name=_var_key,
                title=f"{_base_sc.title.split('(')[0].strip()} [{_lbl}]",
                description=f"{_base_sc.description} ({_lbl})",
                active_ues=_base_sc.active_ues,
                idle_ues=_base_sc.idle_ues,
                rules=_base_sc.rules,
                explanation=_base_sc.explanation,
                expected_shares=_base_sc.expected_shares,
                test_mode=_base_sc.test_mode,
                direction=_d,
                protocol=_p,
            )


def run_cmd(cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def is_container_running(name: str) -> bool:
    res = run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return res.returncode == 0 and res.stdout.strip().lower() == "true"


def print_step(step_num: int, total_steps: int, title: str, done: bool = False, msg: str = ""):
    if not done:
        sys.stdout.write(f"  [{step_num}/{total_steps}] {title:<45} \033[1;33m[...]\033[0m")
        sys.stdout.flush()
    else:
        detail = f" - {msg}" if msg else ""
        sys.stdout.write(f"\r  [{step_num}/{total_steps}] {title:<45} \033[1;32m[✓]\033[0m{detail}\n")
        sys.stdout.flush()


def run_cmd_streaming_box(cmd: list[str], step_title: str) -> tuple[int, str]:
    """Execute command while streaming output in an in-place rolling box sized to the window height."""
    term_cols, term_lines = shutil.get_terminal_size((80, 24))
    box_width = max(60, min(term_cols - 4, 90))
    max_visible_lines = max(4, min(10, term_lines - 14))

    title_text = f" LIVE PROGRESS LOG: {step_title[:35]} "
    header_dashes = box_width - 2 - len(title_text)
    left_dashes = max(2, header_dashes // 2)
    right_dashes = max(2, header_dashes - left_dashes)
    header = f"  \033[1;36m┌{'─' * left_dashes}{title_text}{'─' * right_dashes}┐\033[0m"

    print(f"\n{header}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    full_output = []
    recent_lines = [""] * max_visible_lines
    rendered = False

    try:
        for line in iter(proc.stdout.readline, ""):
            line_str = line.rstrip()
            if line_str:
                full_output.append(line_str)
                recent_lines.pop(0)
                recent_lines.append(line_str)

                # Overwrite visible lines in place
                if rendered:
                    sys.stdout.write(f"\033[{max_visible_lines + 1}F")

                for r_line in recent_lines:
                    trimmed = r_line[: box_width - 4]
                    sys.stdout.write(f"  \033[1;30m│\033[0m {trimmed:<{box_width - 4}} \033[1;30m│\033[0m\n")

                footer_text = " [RUNNING ...] "
                f_dashes = box_width - 2 - len(footer_text)
                sys.stdout.write(f"  \033[1;36m└{'─' * (f_dashes // 2)}\033[1;33m{footer_text}\033[1;36m{'─' * (f_dashes - f_dashes // 2)}┘\033[0m\n")
                sys.stdout.flush()
                rendered = True

        proc.wait()
    except Exception as e:
        proc.kill()
        print(f"  \033[1;31m│ Error during execution: {e}\033[0m")

    # Final footer update
    status_tag = " [COMPLETED ✓] " if proc.returncode == 0 else " [FAILED ✗] "
    f_dashes = box_width - 2 - len(status_tag)
    color = "\033[1;32m" if proc.returncode == 0 else "\033[1;31m"
    if rendered:
        sys.stdout.write(f"\033[1F")
    sys.stdout.write(f"  \033[1;36m└{'─' * (f_dashes // 2)}{color}{status_tag}\033[1;36m{'─' * (f_dashes - f_dashes // 2)}┘\033[0m\n\n")
    sys.stdout.flush()
    return proc.returncode, "\n".join(full_output)


STATE_FILE = Path("/tmp/rfsim_last_prepared_scenario.txt")


def get_scenario_base_group(name: str) -> str:
    """Map scenario name or numeric alias to its fundamental gNB slicing configuration baseline."""
    if name in NUMERIC_ALIASES:
        name = NUMERIC_ALIASES[name]
    for suffix in ("_dl_udp", "_dl_tcp", "_ul_udp", "_ul_tcp", "_udp", "_tcp"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    for suffix in ("_idle", "_full"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    if name.startswith("pf"):
        return "pf_only"
    if name.startswith("as_no_slice"):
        return "as_no_slice"
    return name


E2AP_BASELINE_SCENARIO = "as_no_slice"  # first bringup: dedicated=0, min=0, max=100 on all slices


def is_pf_scenario_name(name: Optional[str]) -> bool:
    base = get_scenario_base_group(name or "")
    return base.startswith("pf") or base in ("pf_only", "pf")


def is_pf_scenario(sc: TestScenario) -> bool:
    return is_pf_scenario_name(sc.name) or not sc.rules and str(sc.name).startswith("pf")


def prepare_state_key(slice_config_mode: str, scenario_name: Optional[str]) -> str:
    """State-file key: E2AP uses pf vs nsboth; startup uses YAML slice group."""
    if slice_config_mode == SLICE_CONFIG_E2AP:
        return "e2ap:pf" if is_pf_scenario_name(scenario_name) else "e2ap:nsboth"
    base_group = get_scenario_base_group(scenario_name or "as_no_slice")
    return f"startup:{base_group}"


def e2ap_rules_for(sc: TestScenario) -> list[SliceRule]:
    """E2 SET payload. PF scenarios have no slice policy (empty)."""
    if is_pf_scenario(sc) or not sc.rules:
        return []
    return list(sc.rules)


def wait_xapp_api(timeout_s: float = 45.0) -> bool:
    """Poll FlexRIC xApp health / slices REST until it answers or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for url in (XAPP_HEALTH_URL, XAPP_API_URL):
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception:
                continue
        time.sleep(1.0)
    return False


def ensure_xapp() -> bool:
    """Start nws-xapp-slice-monitor if needed and wait for REST on :18080."""
    if wait_xapp_api(timeout_s=2.0):
        print("  -> FlexRIC xApp REST API is ready.")
        return True
    if not XAPP_COMPOSE.is_file():
        print(f"  -> [FAIL] xApp compose missing: {XAPP_COMPOSE}")
        return False
    print("  -> Starting FlexRIC xApp (nws-xapp-slice-monitor)...")
    ret, _ = run_cmd_streaming_box(
        ["docker", "compose", "-f", str(XAPP_COMPOSE), "up", "-d"],
        "Starting FlexRIC xApp",
    )
    if ret != 0:
        print("\033[1;31m[FAIL] Failed to start FlexRIC xApp compose\033[0m")
        return False
    if not wait_xapp_api(timeout_s=60.0):
        print("\033[1;31m[FAIL] FlexRIC xApp REST API did not become ready on :18080\033[0m")
        logs = run_cmd(["docker", "logs", "--tail", "40", XAPP_CONTAINER])
        text = (logs.stdout or logs.stderr or "").strip()
        if text:
            print("  -> nws-xapp-slice-monitor logs:")
            for line in text.splitlines()[-20:]:
                print(f"     {line}")
        return False
    print("  -> FlexRIC xApp REST API is ready.")
    return True


def stop_xapp() -> None:
    """Stop leftover xApp so E2 CONTROL cannot override gNB YAML slice config."""
    if is_container_running(XAPP_CONTAINER):
        run_cmd(["docker", "rm", "-f", XAPP_CONTAINER])


def undeploy_testbed() -> int:
    """Stop 5GC, RAN (gNB), and UE containers via bringup.py down --with-core."""
    print("\n========================================================================")
    print("           UNDEPLOY: 5GC + RAN + UEs")
    print("========================================================================")
    cmd = [sys.executable, str(BRINGUP_PY), "down", "--with-core"]
    ret, _ = run_cmd_streaming_box(cmd, "Undeploy 5GC + RAN + UEs")
    stop_xapp()
    if STATE_FILE.is_file():
        try:
            STATE_FILE.unlink()
        except OSError:
            pass
    if ret == 0:
        print("\033[1;32m[OK] Containers undeployed (5GC, RAN, UEs).\033[0m\n")
    else:
        print(f"\033[1;31m[FAIL] Undeploy exited with code {ret}.\033[0m\n")
    return ret


def auto_prepare_testbed(
    num_ues: int = 5,
    scenario_name: Optional[str] = None,
    force_restart: bool = False,
    slice_config_mode: str = SLICE_CONFIG_STARTUP,
) -> bool:
    """Keep 5GC if healthy. Restart gNB/UEs when force_restart or slice baseline changed."""
    mode_label = SLICE_CONFIG_LABELS.get(slice_config_mode, slice_config_mode)
    print("\n========================================================================")
    print("           AUTO-PREPARATION & 5G TESTBED HEALTH CHECK")
    print(f"           Slice config method: {mode_label}")
    print("========================================================================")

    is_pf = is_pf_scenario_name(scenario_name)
    total_steps = 5 if (slice_config_mode == SLICE_CONFIG_E2AP and not is_pf) else 4

    # 1. Check/Bringup 5GC (Reused if already running)
    print_step(1, total_steps, "Checking Open5GS Core (nws-5gc)", done=False)
    if not is_container_running(CORE_CONTAINER):
        ret, _ = run_cmd_streaming_box(
            ["docker", "compose", "-f", str(CORE_COMPOSE), "up", "-d"],
            "Starting Open5GS Core Network",
        )
        if ret != 0:
            print(f"\033[1;31m[FAIL] Failed to start Open5GS Core\033[0m")
            return False
        time.sleep(2)
    print_step(1, total_steps, "Checking Open5GS Core (nws-5gc)", done=True, msg="Running & Healthy (Reused)")

    base_group = get_scenario_base_group(scenario_name or "as_no_slice")
    state_key = prepare_state_key(slice_config_mode, scenario_name)
    last_key = STATE_FILE.read_text().strip() if STATE_FILE.is_file() else ""

    ran_healthy = is_container_running(GNB_CONTAINER)
    all_ues_healthy = all(is_container_running(f"nws-oai-nr-ue{u}") for u in range(1, num_ues + 1))

    # Check PDU sessions on running UEs
    ue_ips = {}
    if ran_healthy and all_ues_healthy:
        for ue_idx in range(1, num_ues + 1):
            ue_name = f"nws-oai-nr-ue{ue_idx}"
            res = run_cmd(["docker", "exec", ue_name, "ip", "-4", "addr", "show"])
            ip_match = re.search(r"inet\s+(10\.45\.\d+\.\d+)", res.stdout)
            if ip_match:
                ue_ips[ue_idx] = ip_match.group(1)

    has_active_pdus = (len(ue_ips) == num_ues)
    can_reuse = (
        not force_restart
        and ran_healthy
        and all_ues_healthy
        and has_active_pdus
        and (last_key == state_key)
    )

    # 2. Check if we can reuse existing RAN & UEs without restart
    if can_reuse:
        print_step(
            2,
            total_steps,
            f"Checking OAI gNB ({GNB_CONTAINER})",
            done=True,
            msg=f"Running (Reused - {state_key})",
        )
        print_step(
            3,
            total_steps,
            f"Verifying UEs (nws-oai-nr-ue1..{num_ues})",
            done=True,
            msg=f"PDU Sessions Active ({', '.join(ue_ips.values())})",
        )
        if slice_config_mode == SLICE_CONFIG_E2AP and not is_pf:
            print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=False)
            if not ensure_xapp():
                print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=True, msg="FAILED")
                return False
            print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=True, msg="Ready")
            print_step(
                5,
                total_steps,
                "5G End-to-End Testbed Readiness",
                done=True,
                msg="gNB/UEs kept running; slice change via xApp E2AP (no restart)",
            )
        else:
            if slice_config_mode == SLICE_CONFIG_STARTUP:
                stop_xapp()
            ready_msg = (
                "Ready (sch=PF reused — no NS slice log expected)"
                if is_pf
                else "Ready for Slicing Tests (No Restart Needed)"
            )
            print_step(4, total_steps, "5G End-to-End Testbed Readiness", done=True, msg=ready_msg)
        print("========================================================================\n")
        return True

    # Startup YAML path: stop xApp so leftover E2 SET cannot override bringup Slices.
    if slice_config_mode == SLICE_CONFIG_STARTUP:
        stop_xapp()

    # 2. Fresh RAN & UE Bringup via bringup.py if config changed or containers down
    sch_type = "PF" if is_pf else "NSBOTH"
    if slice_config_mode == SLICE_CONFIG_E2AP and is_pf:
        step2_title = "Restart gNB/UEs (sch=PF, no NS slice layer)"
    elif slice_config_mode == SLICE_CONFIG_E2AP:
        step2_title = "Restart gNB/UEs (NSBOTH baseline 0/0/100%)"
    elif is_pf:
        step2_title = "Configuring RAN Baseline (sch=PF)"
    else:
        step2_title = f"Configuring Slicing Baseline & Bringup ({base_group})"
    print_step(2, total_steps, step2_title, done=False)
    bringup_cmd = [
        sys.executable,
        str(BRINGUP_PY),
        "--ues", str(num_ues),
        "--sch", sch_type,
    ]
    if slice_config_mode == SLICE_CONFIG_STARTUP:
        bringup_cmd.append("--no-ric")
        if scenario_name and not is_pf:
            bringup_cmd.extend(["--scenario", scenario_name])
    elif not is_pf:
        # E2AP NSBOTH: 0/0/100% on all slices; RIC stays up for xApp.
        bringup_cmd.extend(["--scenario", E2AP_BASELINE_SCENARIO])
    ret, _ = run_cmd_streaming_box(bringup_cmd, f"OAI gNB & UE Bringup ({state_key})")
    if ret != 0:
        print_step(
            2,
            total_steps,
            f"Configuring Slicing Baseline & Bringup ({base_group})",
            done=True,
            msg=f"FAILED (bringup exited with code {ret})",
        )
        print(f"\033[1;31m[FAIL] Failed to bring up testbed with {state_key}.\033[0m\n")
        return False

    STATE_FILE.write_text(state_key)
    print_step(2, total_steps, "Starting OAI gNB (nws-oai-gnb)", done=True, msg="Running (rfsim sync OK)")

    # 3. Verify PDU Sessions strictly on all UEs
    print_step(3, total_steps, f"Verifying UEs (nws-oai-nr-ue1..{num_ues})", done=False)
    ue_ips = {}
    for ue_idx in range(1, num_ues + 1):
        ue_name = f"nws-oai-nr-ue{ue_idx}"
        res = run_cmd(["docker", "exec", ue_name, "ip", "-4", "addr", "show", "oaitun_ue1"])
        ip_match = re.search(r"inet\s+(10\.45\.\d+\.\d+)", res.stdout)
        if ip_match:
            ue_ips[ue_idx] = ip_match.group(1)

    if len(ue_ips) < num_ues:
        missing = [f"UE{u}" for u in range(1, num_ues + 1) if u not in ue_ips]
        print_step(
            3,
            total_steps,
            f"Verifying UEs (nws-oai-nr-ue1..{num_ues})",
            done=True,
            msg=f"FAILED: Missing PDU on {', '.join(missing)}",
        )
        print(f"\033[1;31m[ERROR] Initial conditions not met: UEs {', '.join(missing)} failed to attach PDU session.\033[0m\n")
        return False

    print_step(
        3,
        total_steps,
        f"Verifying UEs (nws-oai-nr-ue1..{num_ues})",
        done=True,
        msg=f"PDU Sessions Active ({', '.join(ue_ips.values())})",
    )

    if slice_config_mode == SLICE_CONFIG_E2AP and not is_pf:
        print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=False)
        if not ensure_xapp():
            print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=True, msg="FAILED")
            return False
        print_step(4, total_steps, "FlexRIC xApp E2AP REST", done=True, msg="Ready")
        print_step(
            5,
            total_steps,
            "5G End-to-End Testbed Readiness",
            done=True,
            msg="Baseline 0/0/100%; later NS cases in this run change slice via xApp (no gNB/UE restart)",
        )
    elif is_pf:
        print_step(4, total_steps, "5G End-to-End Testbed Readiness", done=True, msg="Ready (sch=PF — no NS slice log expected)")
    else:
        print_step(4, total_steps, "5G End-to-End Testbed Readiness", done=True, msg="Ready (slice via gNB startup YAML)")
    print("========================================================================\n")
    return True


def setup_test_logging(
    sc: TestScenario,
    direction: str,
    run_dir: Optional[Path] = None,
    test_idx: Optional[str] = None,
    slice_config_mode: str = SLICE_CONFIG_STARTUP,
) -> Path:
    """Create dedicated timestamped log folder for the run and individual test case."""
    if run_dir is None:
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = LOGS_DIR / f"run_{run_ts}"
        run_dir.mkdir(parents=True, exist_ok=True)
        latest_link = LOGS_DIR / "latest"
        if latest_link.is_symlink() or latest_link.exists():
            latest_link.unlink()
        try:
            latest_link.symlink_to(run_dir.relative_to(LOGS_DIR))
        except Exception:
            pass

    prefix = f"{test_idx}_" if test_idx else ""
    test_log_dir = run_dir / f"{prefix}{sc.name}"
    test_log_dir.mkdir(parents=True, exist_ok=True)

    method = SLICE_CONFIG_LABELS.get(slice_config_mode, slice_config_mode)
    summary_file = test_log_dir / "test_summary.log"
    with open(summary_file, "w") as f:
        f.write("========================================================================\n")
        f.write(f"3GPP Network Slicing Live rfsim Test: {sc.title}\n")
        f.write("========================================================================\n")
        f.write(f"Timestamp:   {datetime.datetime.now().isoformat()}\n")
        f.write(f"Scenario:    {sc.name}\n")
        f.write(f"Direction:   {direction.upper()}\n")
        f.write(f"Slice config:{method}\n")
        f.write(f"Active UEs:  {sc.active_ues}\n")
        f.write(f"Idle UEs:    {sc.idle_ues}\n")
        f.write(f"Description: {sc.description}\n")
        f.write(f"Explanation: {sc.explanation}\n")
        f.write("\nApplied Slice Rules:\n")
        for r in sc.rules:
            f.write(f"  - Slice (SST={r.sst}, SD={r.sd}): dedicated={r.dedicated}%, min={r.min_ratio}%, max={r.max_ratio}%\n")
        f.write("========================================================================\n")

    return test_log_dir


def apply_xapp_policy(rules: list[SliceRule], log_dir: Path, *, required: bool = False) -> bool:
    """Apply slice policy via FlexRIC xApp E2AP REST API (PUT /api/v1/slices)."""
    if not rules:
        return True

    payload = {"slices": []}
    for r in rules:
        dirs = ["dl", "ul"] if r.direction == "both" else [r.direction]
        for d in dirs:
            payload["slices"].append({
                "sst": r.sst,
                "sd": r.sd,
                "direction": d,
                "dedicated": r.dedicated,
                "min": r.min_ratio,
                "max": r.max_ratio,
            })

    with open(log_dir / "xapp_policy_applied.json", "w") as f:
        json.dump(payload, f, indent=2)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        XAPP_API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            try:
                (log_dir / "xapp_policy_response.json").write_text(body)
            except Exception:
                pass
            if 200 <= resp.status < 300:
                print("  -> Applied slice policy via FlexRIC xApp E2AP REST API.")
                time.sleep(1.5)
                return True
            msg = f"xApp PUT returned HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
        try:
            (log_dir / "xapp_policy_response.json").write_text(body or str(e))
        except Exception:
            pass
        msg = f"FlexRIC xApp API HTTP {e.code}: {body[:200] or e.reason}"
    except Exception as e:
        msg = f"FlexRIC xApp API not available ({e})"

    if required:
        print(f"  -> \033[1;31m[FAIL] {msg}\033[0m")
        return False
    print(f"  -> {msg}. Policy will use current gNB configuration.")
    return False


def setup_iperf_servers(direction: str):
    """Ensure clean iperf3 servers are running on UPF Core container with per-port logging."""
    for proc in ("iperf3", "iperf", "ping", "tail"):
        run_cmd(["docker", "exec", CORE_CONTAINER, "killall", "-9", proc])
        run_cmd(["docker", "exec", CORE_CONTAINER, "pkill", "-9", "-f", proc])
    for ue in range(1, 6):
        run_cmd(["docker", "exec", f"nws-oai-nr-ue{ue}", "killall", "-9", "iperf3", "ping"])
        run_cmd(["docker", "exec", f"nws-oai-nr-ue{ue}", "pkill", "-9", "-f", "iperf3"])
        run_cmd(["docker", "exec", f"nws-oai-nr-ue{ue}", "pkill", "-9", "-f", "ping"])
    time.sleep(0.4)

    for u in range(1, 6):
        port = 5200 + u
        run_cmd(["docker", "exec", CORE_CONTAINER, "rm", "-f", f"/tmp/iperf_server_{u}.log"])
        loop_cmd = f"while true; do iperf3 -s -p {port} -i 1 --forceflush >> /tmp/iperf_server_{u}.log 2>&1; sleep 0.2; done"
        run_cmd(["docker", "exec", "-d", CORE_CONTAINER, "sh", "-c", loop_cmd])
    time.sleep(0.6)


def get_mac_stats() -> str:
    """Fetch gNB mac stats from container or telnet."""
    for port in (19090, 9090):
        try:
            tn = telnetlib.Telnet("127.0.0.1", port, timeout=2.0)
            tn.read_until(b"softmodem_gnb>", timeout=2.0)
            tn.write(b"mac stats\n")
            output = tn.read_until(b"softmodem_gnb>", timeout=3.0).decode("utf-8", errors="ignore")
            tn.close()
            if output:
                return output
        except Exception:
            continue
    res = run_cmd(["docker", "logs", "--tail", "80", GNB_CONTAINER])
    return res.stdout


_NS_SLICE_LINE_RE = re.compile(
    r"slice SST 0x([0-9a-fA-F]+) SD 0x([0-9a-fA-F]+):\s+"
    r"latest\s+(\d+)\s+PRBs.*?avg\s+([\d.]+)\s+PRBs\s+\(([\d.]+)%\)\s+"
    r"require\s+(\d+)\s+dedicated/min/max\s+([\d.]+)/([\d.]+)/([\d.]+)%",
    re.IGNORECASE,
)


def _sd_int(sd: str | int) -> int:
    if isinstance(sd, int):
        return sd
    return int(str(sd).strip().lower().replace("0x", ""), 16)


def expected_ns_policy(
    rules: list[SliceRule],
) -> dict[tuple[str, int], tuple[float, float, float]]:
    """Map (direction, sd) -> (dedicated, min, max). Skips default SD 0xffffff."""
    expected: dict[tuple[str, int], tuple[float, float, float]] = {}
    for r in rules:
        sd = _sd_int(r.sd)
        if sd == 0xFFFFFF:
            continue
        dirs = ["dl", "ul"] if r.direction == "both" else [r.direction.lower()]
        for d in dirs:
            expected[(d, sd)] = (float(r.dedicated), float(r.min_ratio), float(r.max_ratio))
    return expected


def extract_ns_prb_blocks(text: str) -> str:
    """Keep the latest NS UL / NS DL PRB allocation stanzas from mac stats."""
    chunks: list[str] = []
    pattern = re.compile(
        r"(NS (?:UL|DL) PRB allocation[^\n]*\n(?:[ \t]*slice SST[^\n]*\n)+)",
        re.IGNORECASE,
    )
    last_ul = last_dl = ""
    for m in pattern.finditer(text):
        block = m.group(1).rstrip()
        if "NS UL PRB" in block.upper() or block.lower().startswith("ns ul"):
            last_ul = block
        else:
            last_dl = block
    if last_ul:
        chunks.append(last_ul)
    if last_dl:
        chunks.append(last_dl)
    return "\n".join(chunks)


def parse_ns_prb_policy(text: str) -> dict[tuple[str, int], tuple[float, float, float]]:
    """Parse dedicated/min/max % per (ul|dl, sd) from mac stats NS PRB allocation."""
    found: dict[tuple[str, int], tuple[float, float, float]] = {}
    for direction, header in (("ul", "NS UL PRB allocation"), ("dl", "NS DL PRB allocation")):
        parts = re.split(re.escape(header), text, flags=re.IGNORECASE)
        if len(parts) < 2:
            continue
        block = parts[-1]
        nxt = re.search(r"\nNS (?:UL|DL) PRB allocation", block, flags=re.IGNORECASE)
        if nxt:
            block = block[: nxt.start()]
        for m in _NS_SLICE_LINE_RE.finditer(block):
            sd = int(m.group(2), 16)
            if sd == 0xFFFFFF:
                continue
            found[(direction, sd)] = (float(m.group(7)), float(m.group(8)), float(m.group(9)))
    return found


def _policy_close(
    got: tuple[float, float, float], want: tuple[float, float, float], tol: float = 0.51
) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(got, want))


def wait_ns_prb_policy_applied(
    rules: list[SliceRule],
    log_dir: Path,
    timeout_s: float = 45.0,
) -> bool:
    """Poll gNB mac stats until NS UL/DL dedicated/min/max match the applied xApp policy."""
    expected = expected_ns_policy(rules)
    if not expected:
        return True

    print("  -> Waiting for gNB NS UL/DL PRB allocation to show the new dedicated/min/max...")
    want_lines = []
    for (d, sd), (ded, mn, mx) in sorted(expected.items()):
        want_lines.append(f"    {d.upper()} SD 0x{sd:06x}: dedicated/min/max {ded:g}/{mn:g}/{mx:g}%")
    print("\n".join(want_lines))

    deadline = time.time() + timeout_s
    last_blocks = ""
    last_found: dict[tuple[str, int], tuple[float, float, float]] = {}
    while time.time() < deadline:
        stats = get_mac_stats()
        last_blocks = extract_ns_prb_blocks(stats)
        last_found = parse_ns_prb_policy(stats)
        missing = []
        for key, want in expected.items():
            got = last_found.get(key)
            if got is None or not _policy_close(got, want):
                d, sd = key
                got_s = f"{got[0]:g}/{got[1]:g}/{got[2]:g}%" if got else "(missing)"
                missing.append(f"{d.upper()} SD 0x{sd:06x}: got {got_s}, want {want[0]:g}/{want[1]:g}/{want[2]:g}%")
        if not missing:
            print("  -> NS PRB allocation matches applied slice config. Starting traffic.")
            if last_blocks:
                print(last_blocks)
            try:
                (log_dir / "ns_prb_policy_verified.log").write_text(
                    last_blocks + "\n" if last_blocks else "matched (no stanza captured)\n"
                )
            except Exception:
                pass
            return True
        time.sleep(1.0)

    print("\033[1;31m  -> [FAIL] NS PRB dedicated/min/max did not match after E2 SET:\033[0m")
    for (d, sd), want in sorted(expected.items()):
        got = last_found.get((d, sd))
        got_s = f"{got[0]:g}/{got[1]:g}/{got[2]:g}%" if got else "(missing)"
        print(f"     {d.upper()} SD 0x{sd:06x}: got {got_s}, want {want[0]:g}/{want[1]:g}/{want[2]:g}%")
    if last_blocks:
        print(last_blocks)
    try:
        (log_dir / "ns_prb_policy_verified.log").write_text(
            "MISMATCH\n" + (last_blocks or "") + "\n"
        )
    except Exception:
        pass
    return False


def mac_stats_has_ns_slice_log(text: str) -> bool:
    """True if mac stats / gNB log includes NS slice PRB allocation (SCHE_NS)."""
    if not text:
        return False
    if re.search(r"NS (?:UL|DL) PRB allocation", text, flags=re.IGNORECASE):
        return True
    if re.search(
        r"slice SST\s+0x[0-9a-fA-F]+\s+SD\s+0x[0-9a-fA-F]+:.*dedicated/min/max",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def wait_no_ns_slice_log(log_dir: Path, timeout_s: float = 12.0) -> bool:
    """For sch=PF: confirm mac stats has no NS UL/DL slice PRB allocation log."""
    print("  -> Checking mac stats: PF scheduler must not print NS slice PRB allocation...")
    deadline = time.time() + timeout_s
    last_stats = ""
    clean_reads = 0
    while time.time() < deadline:
        last_stats = get_mac_stats()
        if mac_stats_has_ns_slice_log(last_stats):
            blocks = extract_ns_prb_blocks(last_stats)
            print("\033[1;31m  -> [FAIL] NS slice PRB log present (expected none for PF / test 000):\033[0m")
            if blocks:
                print(blocks)
            try:
                (log_dir / "ns_prb_policy_verified.log").write_text(
                    "UNEXPECTED NS SLICE LOG (PF)\n" + (blocks or last_stats[-2000:]) + "\n"
                )
            except Exception:
                pass
            return False
        if last_stats.strip():
            clean_reads += 1
            if clean_reads >= 2:
                print("  -> No NS UL/DL slice PRB log (sch=PF). Starting traffic.")
                try:
                    (log_dir / "ns_prb_policy_verified.log").write_text(
                        "OK: no NS UL/DL PRB allocation log (PF scheduler)\n"
                    )
                except Exception:
                    pass
                return True
        time.sleep(1.0)
    print("  -> No NS slice PRB log observed. Starting traffic.")
    try:
        (log_dir / "ns_prb_policy_verified.log").write_text(
            "OK: no NS UL/DL PRB allocation log (PF scheduler)\n"
        )
    except Exception:
        pass
    return True


def extract_latest_sum_mbps(text: str, streams: int = 5) -> Optional[float]:
    """Extract the latest interval [SUM] bitrate from iperf3 output, filtering sub-second connect spikes."""
    if not text:
        return None
    # 1. Prioritize [SUM] lines with valid standard interval (duration >= 0.5s)
    sum_matches = re.findall(r"\[SUM\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)", text)
    if sum_matches:
        valid_rates = []
        for t1, t2, rate_str, unit in sum_matches:
            duration = float(t2) - float(t1)
            if duration < 0.5:
                continue
            rate = float(rate_str)
            if "Gbits" in unit:
                rate *= 1000.0
            elif "Kbits" in unit:
                rate /= 1000.0
            if 0.05 <= rate <= 2000.0:
                valid_rates.append(rate)
        if valid_rates:
            return valid_rates[-1]

    # 2. Fallback for single stream (-P 1)
    if streams == 1:
        stream_matches = re.findall(r"\[\s*\d+\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)", text)
        if stream_matches:
            valid_rates = []
            for t1, t2, rate_str, unit in stream_matches:
                duration = float(t2) - float(t1)
                if duration < 0.5:
                    continue
                rate = float(rate_str)
                if "Gbits" in unit:
                    rate *= 1000.0
                elif "Kbits" in unit:
                    rate /= 1000.0
                if 0.05 <= rate <= 2000.0:
                    valid_rates.append(rate)
            if valid_rates:
                return valid_rates[-1]
    return None


def parse_iperf_log_throughput(f_text: str, duration: int = 30, streams: int = 5) -> Optional[float]:
    """Parse average steady-state throughput from iperf3 log output."""
    if not f_text:
        return None

    # 1. Check for official full summary report: [SUM] 0.00-30.00 sec ... rate (receiver or sender)
    full_summaries = re.findall(
        r"\[SUM\]\s+0\.00-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)",
        f_text,
    )
    if full_summaries:
        for dur_str, rate_str, unit in full_summaries:
            if float(dur_str) >= (duration * 0.75):
                rate_val = float(rate_str)
                if "Gbits" in unit:
                    rate_val *= 1000.0
                elif "Kbits" in unit:
                    rate_val /= 1000.0
                if rate_val > 0.05:
                    return rate_val

    # 2. Parse all [SUM] interval reports (or single-stream intervals if streams == 1)
    if streams > 1:
        pattern = r"\[SUM\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)"
    else:
        pattern = r"\[\s*\d+\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)"

    matches = re.findall(pattern, f_text)
    if not matches and streams > 1:
        matches = re.findall(r"\[\s*\d+\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec.*?([\d\.]+)\s+([KMGT]?bits/sec)", f_text)

    if matches:
        valid_rates = []
        for t1, t2, rate_str, unit in matches:
            dt = float(t2) - float(t1)
            if dt < 0.5:
                continue
            rate = float(rate_str)
            if "Gbits" in unit:
                rate *= 1000.0
            elif "Kbits" in unit:
                rate /= 1000.0
            if 0.05 <= rate <= 2000.0:
                valid_rates.append(rate)

        if valid_rates:
            # Drop the first 1-2 intervals (connect/ramp-up) if we have enough samples
            if len(valid_rates) > 4:
                steady_rates = valid_rates[2:]
            elif len(valid_rates) > 2:
                steady_rates = valid_rates[1:]
            else:
                steady_rates = valid_rates
            return sum(steady_rates) / len(steady_rates)

    return None


def sample_live_traffic_countdown(sc: TestScenario, direction: str, duration: int, mac_log: Path, log_dir: Path) -> dict[int, float]:
    """Sample live traffic: for DL from UE receiver, for UL from Server receiver, with real-time countdown."""
    print(f"\n--- [LIVE SAMPLING FROM TMUX SCREENS ({duration}s)] ---")
    ue_mbps_history = {u: [] for u in range(1, 6)}
    latest_mbps = {u: 0.0 for u in range(1, 6)}
    streams = getattr(sc, "streams", 5)

    for remaining in range(duration, 0, -1):
        # 1. Capture gNB MAC stats for logging
        gnb_stats_out = run_cmd(["docker", "logs", "--tail", "25", GNB_CONTAINER]).stdout
        if gnb_stats_out:
            with open(mac_log, "a") as f:
                f.write(f"\n--- [{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] gNB STATS ---\n")
                f.write(gnb_stats_out + "\n")

        # 2. Capture throughput: for UL from Tab 1 (servers) receiver logs, for DL from Tab 2 (clients) receiver logs
        for u in sc.active_ues:
            val = None
            if direction == "ul":
                # For UL, get throughput from Server (UPF Core) receiver logs and Tab 1 pane
                pane_id = f"{TMUX_SESSION}:1.{u-1}"
                p_capture = run_cmd(["tmux", "capture-pane", "-p", "-S", "-40", "-t", pane_id]).stdout
                val = extract_latest_sum_mbps(p_capture, streams)

                if val is None:
                    srv_out = run_cmd(["docker", "exec", CORE_CONTAINER, "cat", f"/tmp/iperf_server_{u}.log"]).stdout
                    val = extract_latest_sum_mbps(srv_out, streams)
            else:
                # For DL, get throughput from UE receiver pane (Tab 2) and logs
                pane_id = f"{TMUX_SESSION}:2.{u-1}"
                p_capture = run_cmd(["tmux", "capture-pane", "-p", "-S", "-40", "-t", pane_id]).stdout
                val = extract_latest_sum_mbps(p_capture, streams)

                if val is None:
                    log_f = log_dir / f"ue{u}_traffic.log"
                    if log_f.is_file():
                        try:
                            val = extract_latest_sum_mbps(log_f.read_text(errors="ignore"), streams)
                        except Exception:
                            pass

            if val is not None and val > 0.05:
                latest_mbps[u] = val
                ue_mbps_history[u].append(val)

        # 3. Check gNB goodput logs if any active UE is still 0
        if any(latest_mbps[u] == 0.0 for u in sc.active_ues):
            gnb_out = run_cmd(["docker", "logs", "--tail", "35", GNB_CONTAINER]).stdout
            for m in re.finditer(r"CU-UE-ID\s+(\d+).*?goodput\s+DL\s+([\d\.]+)\s+UL\s+([\d\.]+)\s+Mbps", gnb_out, re.DOTALL):
                ue_id = int(m.group(1))
                rate = float(m.group(3)) if direction == "ul" else float(m.group(2))
                if ue_id in sc.active_ues and rate > 0:
                    latest_mbps[ue_id] = rate
                    ue_mbps_history[ue_id].append(rate)

        # Format display line across all 5 UEs
        parts = []
        is_ping = getattr(sc, "test_mode", "iperf") == "ping"
        for u in range(1, 6):
            if u in sc.active_ues:
                if is_ping:
                    # Check latest ping rtt
                    log_f = log_dir / f"ue{u}_traffic.log"
                    latest_rtt = "OK"
                    if log_f.is_file():
                        try:
                            f_text = log_f.read_text(errors="ignore")
                            rtts = re.findall(r"time=([\d\.]+)\s*ms", f_text)
                            if rtts:
                                latest_rtt = f"{float(rtts[-1]):.0f}ms"
                        except Exception:
                            pass
                    parts.append(f"UE{u}:{latest_rtt}")
                else:
                    parts.append(f"UE{u}:{latest_mbps[u]:4.1f}M")
            elif u in sc.idle_ues:
                parts.append(f"UE{u}:[IDLE]")
        status_line = " | ".join(parts)
        mode_label = "Live 5-UE ping monitoring..." if is_ping else "Live 5-UE throughput monitoring..."
        print(f"\r  [{remaining:2d}s remaining]  {status_line:<45} | {mode_label} ", end="", flush=True)
        time.sleep(1)

    run_cmd(["tmux", "set-option", "-t", TMUX_SESSION, "status-right", "#[fg=#a6e3a1][COMPLETED] Tabs: [0:servers] [1:clients] #[default]"])
    print(f"\r  [ 0s remaining]  Sampling complete! \033[1;32m[✓]\033[0m                                                    \n")

    # Compute average measured throughput over the sampling period from logs
    avg_mbps = {}
    for ue_idx in range(1, 6):
        if ue_idx in sc.active_ues:
            # Check if final summary report exists in log file or core server log
            final_rate = None
            f_text = ""
            if direction == "ul":
                f_text = run_cmd(["docker", "exec", CORE_CONTAINER, "cat", f"/tmp/iperf_server_{ue_idx}.log"]).stdout
                if not f_text:
                    log_f = log_dir / f"ue{ue_idx}_traffic.log"
                    if log_f.is_file():
                        try:
                            f_text = log_f.read_text(errors="ignore")
                        except Exception:
                            pass
            else:
                log_f = log_dir / f"ue{ue_idx}_traffic.log"
                if log_f.is_file():
                    try:
                        f_text = log_f.read_text(errors="ignore")
                    except Exception:
                        pass

            if f_text:
                final_rate = parse_iperf_log_throughput(f_text, duration, streams)

            if final_rate is not None and final_rate > 0.05:
                avg_mbps[ue_idx] = final_rate
            elif ue_mbps_history[ue_idx]:
                avg_mbps[ue_idx] = sum(ue_mbps_history[ue_idx]) / len(ue_mbps_history[ue_idx])
            else:
                avg_mbps[ue_idx] = latest_mbps[ue_idx]
        else:
            avg_mbps[ue_idx] = 0.0

    return avg_mbps


def get_traffic_params(sc: TestScenario, direction: str | None = None) -> dict:
    """Build iperf3 CLI flags from scenario (UDP: -u -b per stream + -P parallel)."""
    dir_val = getattr(sc, "direction", direction or "ul")
    proto_val = getattr(sc, "protocol", "udp")
    streams = getattr(sc, "streams", 5)
    rev_flag = "-R" if dir_val == "dl" else ""
    if proto_val == "udp":
        bitrate = getattr(sc, "bitrate", None) or ("10M" if dir_val == "ul" else "20M")
        proto_flag = f"-u -b {bitrate} -P {streams}"
    else:
        bitrate = None
        proto_flag = f"-P {streams}"
    return {
        "dir": dir_val,
        "proto": proto_val,
        "bitrate": bitrate,
        "streams": streams,
        "proto_flag": proto_flag,
        "rev_flag": rev_flag,
    }


def format_iperf3_docker_cmd(ue_idx: int, sc: TestScenario, duration: int, direction: str | None = None) -> str:
    p = get_traffic_params(sc, direction)
    port = 5200 + ue_idx
    ue_ip = f"10.45.0.{30 + ue_idx}"
    parts = [
        f"docker exec nws-oai-nr-ue{ue_idx} iperf3 -c {UPF_IP} -B {ue_ip} -p {port}",
        p["rev_flag"],
        p["proto_flag"],
        "--connect-timeout 5000",
        "--forceflush",
        "-i 1",
        f"-t {duration}",
    ]
    return " ".join(part for part in parts if part)


def format_traffic_command(ue_idx: int, sc: TestScenario, duration: int, direction: str | None = None) -> str:
    if getattr(sc, "test_mode", "iperf") == "ping":
        return f"docker exec nws-oai-nr-ue{ue_idx} ping -c {duration} -i 1 {UPF_IP}"
    return format_iperf3_docker_cmd(ue_idx, sc, duration, direction)


def parse_gnb_ue_stats(text: str) -> dict[int, dict]:
    """Parse per-UE gNB MAC stats keyed by CU-UE-ID (1..5)."""
    ue_stats: dict[int, dict] = {}
    current_ue_id: int | None = None
    for line in text.splitlines():
        if line.startswith("UE RNTI "):
            m = re.search(
                r"UE RNTI ([0-9a-fA-F]{4}) CU-UE-ID (\d+).*?(in-sync|out-of-sync)"
                r"(?:.*average RSRP (-?\d+) \((\d+) meas\))?",
                line,
            )
            if not m:
                continue
            current_ue_id = int(m.group(2))
            ue_stats[current_ue_id] = {
                "rnti": m.group(1).lower(),
                "sync": m.group(3),
                "rsrp": int(m.group(4)) if m.group(4) else None,
                "rsrp_meas": int(m.group(5)) if m.group(5) else 0,
            }
            continue
        if current_ue_id is None:
            continue
        cur = ue_stats[current_ue_id]
        if "dlsch_rounds" in line:
            m = re.search(
                r"dlsch_errors (\d+).*pucch0_DTX (\d+).*RSSI ([-\d.]+).*BLER ([\d.]+).*CCE fail (\d+)",
                line,
            )
            if m:
                cur.update({
                    "dl_errors": int(m.group(1)),
                    "pucch_dtx": int(m.group(2)),
                    "dl_rssi": float(m.group(3)),
                    "dl_bler": float(m.group(4)),
                    "dl_cce_fail": int(m.group(5)),
                })
        elif "ulsch_rounds" in line:
            m = re.search(
                r"ulsch_errors (\d+).*ulsch_DTX (\d+).*BLER ([\d.]+).*SNR ([\d.]+).*RSSI ([-\d.]+) CCE fail (\d+)",
                line,
            )
            if m:
                cur.update({
                    "ul_errors": int(m.group(1)),
                    "ulsch_dtx": int(m.group(2)),
                    "ul_bler": float(m.group(3)),
                    "ul_snr": float(m.group(4)),
                    "ul_rssi": float(m.group(5)),
                    "ul_cce_fail": int(m.group(6)),
                })
        elif "goodput" in line:
            m = re.search(r"goodput DL\s+([\d.]+) UL\s+([\d.]+)", line)
            if m:
                cur["dl_goodput_mbps"] = float(m.group(1))
                cur["ul_goodput_mbps"] = float(m.group(2))
    return ue_stats


def parse_gnb_slice_prb_stats(text: str, direction: str = "dl") -> dict[int, dict]:
    """Calculate mean of average PRB allocations across all active snapshots in the test window."""
    target_header = "NS DL PRB allocation" if direction.lower() == "dl" else "NS UL PRB allocation"
    sections = text.split(target_header)
    if len(sections) < 2:
        return {}

    all_parsed_snapshots = []
    for sec in sections[1:]:
        snapshot = {}
        for line in sec.splitlines():
            if ("NS " in line and "PRB allocation" in line) or line.startswith("UE RNTI") or line.startswith("Frame.Slot"):
                if snapshot:
                    break
            m = re.search(
                r"slice SST 0x([0-9a-fA-F]+) SD 0x([0-9a-fA-F]+):\s+latest\s+(\d+)\s+PRBs.*?avg\s+([\d.]+)\s+PRBs\s+\(([\d.]+)%\)\s+require\s+(\d+)\s+dedicated/min/max\s+([\d.]+)/([\d.]+)/([\d.]+)%",
                line,
            )
            if m:
                sst = int(m.group(1), 16)
                sd = int(m.group(2), 16)
                slice_idx = sd if sd in (1, 2, 3, 4, 5) else (0 if sd == 0xffffff else None)
                if slice_idx is not None:
                    snapshot[slice_idx] = {
                        "sst": sst,
                        "sd": sd,
                        "latest_prbs": int(m.group(3)),
                        "avg_prbs": float(m.group(4)),
                        "avg_pct": float(m.group(5)),
                        "require_prbs": int(m.group(6)),
                        "dedicated_pct": float(m.group(7)),
                        "min_pct": float(m.group(8)),
                        "max_pct": float(m.group(9)),
                    }
        if snapshot:
            all_parsed_snapshots.append(snapshot)

    if not all_parsed_snapshots:
        return {}

    # Filter snapshots during active traffic
    active_snapshots = [
        s for s in all_parsed_snapshots
        if sum(s.get(u, {}).get("avg_prbs", 0.0) for u in range(1, 6)) > 5.0
    ]
    if not active_snapshots:
        active_snapshots = all_parsed_snapshots

    aggregated_stats: dict[int, dict] = {}
    all_slice_keys = set()
    for s in active_snapshots:
        all_slice_keys.update(s.keys())

    for u in sorted(all_slice_keys):
        u_snaps = [s[u] for s in active_snapshots if u in s]
        if not u_snaps:
            continue
        last = u_snaps[-1]
        mean_avg_prb = sum(x["avg_prbs"] for x in u_snaps) / len(u_snaps)
        mean_avg_pct = sum(x["avg_pct"] for x in u_snaps) / len(u_snaps)
        mean_latest = sum(x["latest_prbs"] for x in u_snaps) / len(u_snaps)
        mean_require = sum(x["require_prbs"] for x in u_snaps) / len(u_snaps)
        aggregated_stats[u] = {
            "sst": last["sst"],
            "sd": last["sd"],
            "latest_prbs": round(mean_latest, 1),
            "avg_prbs": round(mean_avg_prb, 1),
            "avg_pct": round(mean_avg_pct, 1),
            "require_prbs": round(mean_require, 1),
            "dedicated_pct": last["dedicated_pct"],
            "min_pct": last["min_pct"],
            "max_pct": last["max_pct"],
            "samples": len(u_snaps),
        }
    return aggregated_stats


def parse_gnb_event_counts(text: str) -> dict[str, int]:
    """Aggregate gNB log counters for the test window."""
    return {
        "ra_initiated_mac": len(re.findall(r"initiating RA procedure", text, flags=re.I)),
        "ra_initiated_phy": len(re.findall(r"\[RAPROC\].*Initiating RA procedure", text)),
        "ra_c_rnti_complete": len(re.findall(r"RA with C-RNTI", text)),
        "ra_cbra_succeeded": len(re.findall(r"CBRA procedure succeeded", text)),
        "rrc_reestablish": len(re.findall(r"Reestablishment", text)),
        "rlf": len(re.findall(r"RLF detected", text)),
        "abort_harq": len(re.findall(r"abort HARQ", text)),
        "invalid_pdu": len(re.findall(r"Invalid PDU", text)),
        "max_retx_srb": len(re.findall(r"max RETX reached on SRB", text)),
        "max_retx_drb": len(re.findall(r"max RETX reached on DRB", text)),
    }


def fetch_gnb_log_text(log_dir: Path) -> str:
    """Combine saved gNB container log, live docker logs, and telnet mac stats."""
    chunks: list[str] = []
    gnb_log = log_dir / "gnb_container.log"
    if gnb_log.is_file():
        try:
            chunks.append(gnb_log.read_text(errors="ignore"))
        except Exception:
            pass
    res = run_cmd(["docker", "logs", "--tail", "1200", GNB_CONTAINER])
    if res.stdout:
        chunks.append(res.stdout)
    mac_stats = get_mac_stats()
    if mac_stats:
        chunks.append(mac_stats)
    return "\n".join(chunks)


def format_gnb_stats_report(log_dir: Path, direction: str = "dl") -> tuple[list[str], list[str], dict[int, dict]]:
    """Build gNB statistics lines for console (with ANSI) and evaluation.log (plain)."""
    text = fetch_gnb_log_text(log_dir)
    events = parse_gnb_event_counts(text)
    ue_stats = parse_gnb_ue_stats(text)
    slice_prb_stats = parse_gnb_slice_prb_stats(text, direction)

    console: list[str] = []
    plain: list[str] = []
    console.append("  \033[1;36mgNB event counters (test window):\033[0m")
    plain.append("gNB event counters (test window):")
    event_line = (
        f"    RA initiated MAC={events['ra_initiated_mac']} PHY={events['ra_initiated_phy']} | "
        f"RA C-RNTI complete={events['ra_c_rnti_complete']} | CBRA succeeded={events['ra_cbra_succeeded']} | "
        f"RRC reestablish={events['rrc_reestablish']}"
    )
    console.append(event_line)
    plain.append(event_line.strip())
    err_line = (
        f"    Invalid PDU={events['invalid_pdu']} | abort HARQ={events['abort_harq']} | "
        f"RLF={events['rlf']} | max RETX SRB={events['max_retx_srb']} DRB={events['max_retx_drb']}"
    )
    console.append(err_line)
    plain.append(err_line.strip())

    if slice_prb_stats:
        dir_lbl = direction.upper()
        console.append(f"  \033[1;36mgNB NS {dir_lbl} PRB allocation (average over test snapshots):\033[0m")
        plain.append(f"gNB NS {dir_lbl} PRB allocation (average over test snapshots):")
        for s_idx in sorted(slice_prb_stats.keys()):
            st = slice_prb_stats[s_idx]
            s_name = f"Slice {s_idx}" if s_idx > 0 else "Slice 0 (Default)"
            samples_info = f" ({st['samples']} samples)" if st.get("samples") else ""
            line = (
                f"    {s_name}: Avg {st['avg_prbs']:.1f} PRBs ({st['avg_pct']:.1f}%){samples_info} | "
                f"Configured ded/min/max: {st['dedicated_pct']:.1f}/{st['min_pct']:.1f}/{st['max_pct']:.1f}%"
            )
            console.append(line)
            plain.append(line.strip())

    console.append("  \033[1;36mPer-UE gNB radio stats (end of test):\033[0m")
    plain.append("Per-UE gNB radio stats (end of test):")
    for ue_idx in range(1, 6):
        st = ue_stats.get(ue_idx)
        if not st:
            line = f"    UE{ue_idx}: (no mac stats in log)"
            console.append(line)
            plain.append(line.strip())
            continue
        rsrp = f"{st['rsrp']} dBm ({st['rsrp_meas']} meas)" if st.get("rsrp") is not None else "n/a"
        dl_cce = st.get("dl_cce_fail", "?")
        ul_cce = st.get("ul_cce_fail", "?")
        dl_bler = st.get("dl_bler", "?")
        ul_bler = st.get("ul_bler", "?")
        ul_snr = st.get("ul_snr", "?")
        ul_dtx = st.get("ulsch_dtx", "?")
        dl_gp = st.get("dl_goodput_mbps", "?")
        ul_gp = st.get("ul_goodput_mbps", "?")
        line = (
            f"    UE{ue_idx} RNTI {st.get('rnti', '?')} {st.get('sync', '?')} | "
            f"RSRP {rsrp} | DL CCE fail {dl_cce} BLER {dl_bler} | "
            f"UL CCE fail {ul_cce} BLER {ul_bler} SNR {ul_snr} DTX {ul_dtx} | "
            f"gNB goodput DL {dl_gp} UL {ul_gp} Mbps"
        )
        console.append(line)
        plain.append(line.strip())

    try:
        (log_dir / "gnb_radio_stats.log").write_text("\n".join(plain) + "\n")
    except Exception:
        pass
    return console, plain, slice_prb_stats


def evaluate_test_results(
    sc: TestScenario,
    measured_mbps: dict[int, float],
    log_dir: Path,
    duration: int = 30,
    direction: str | None = None,
) -> bool:
    """Step-by-step evaluation of test scenarios and write final verdict in a structured box."""
    print("========================================================================")
    print(f"             EVALUATION & VERDICT: {sc.name.upper()}")
    print("========================================================================")

    traffic_cmd_lines: list[str] = []
    dir_val = getattr(sc, "direction", direction or "ul")
    if getattr(sc, "test_mode", "iperf") == "ping":
        print("  Traffic commands (ping):")
        for u in sc.active_ues:
            cmd = format_traffic_command(u, sc, duration, direction)
            traffic_cmd_lines.append(f"UE{u}: {cmd}")
            print(f"    UE{u}: {cmd}")
    else:
        p = get_traffic_params(sc, direction)
        if p["proto"] == "udp" and p["bitrate"]:
            print(
                f"  iperf3: {p['dir'].upper()} UDP, -b {p['bitrate']}/stream, -P {p['streams']} "
                f"(target ~{p['streams']}×{p['bitrate']} per active UE)"
            )
        else:
            print(f"  iperf3: {p['dir'].upper()} {p['proto'].upper()}, -P {p['streams']} per active UE")
        for u in sc.active_ues:
            cmd = format_iperf3_docker_cmd(u, sc, duration, direction)
            traffic_cmd_lines.append(f"UE{u}: {cmd}")
            print(f"    UE{u}: {cmd}")
    print("")

    gnb_console, gnb_plain, slice_prb_stats = format_gnb_stats_report(log_dir, direction=dir_val)
    for line in gnb_console:
        print(line)
    print("")

    eval_file = log_dir / "evaluation.log"
    passed = True
    eval_lines = []

    if getattr(sc, "test_mode", "iperf") == "ping":
        # Evaluate 5-UE continuous ping test
        step_subidx = 1
        for u in range(1, 6):
            log_f = log_dir / f"ue{u}_traffic.log"
            loss_pct = 100.0
            rtt_ms = 999.0
            f_text = ""
            if log_f.is_file():
                try:
                    f_text = log_f.read_text(errors="ignore")
                except Exception:
                    pass
            if "% packet loss" not in f_text and u <= 2:
                pane_id = f"{TMUX_SESSION}:0.{u}"
                f_text = run_cmd(["tmux", "capture-pane", "-p", "-t", pane_id]).stdout

            if "% packet loss" not in f_text:
                ping_res = run_cmd(["docker", "exec", f"nws-oai-nr-ue{u}", "ping", "-c", "3", "-i", "0.5", UPF_IP])
                f_text = ping_res.stdout

            loss_m = re.search(r"(\d+(?:\.\d+)?)%\s+packet\s+loss", f_text)
            if loss_m:
                loss_pct = float(loss_m.group(1))
            rtt_m = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/", f_text)
            if rtt_m:
                rtt_ms = float(rtt_m.group(1))

            ok = (loss_pct <= 5.0 and rtt_ms < 50.0)
            status = "\033[1;32m[OK ✓]\033[0m" if ok else "\033[1;31m[FAIL ✗]\033[0m"
            msg = f"  │ [5.{step_subidx}] UE{u} (Slice {u}) Ping {UPF_IP}: Loss={loss_pct:4.1f}%, RTT={rtt_ms:4.1f}ms (Exp: <=5% loss, <50ms) -> {status}"
            eval_lines.append((msg, f"[5.{step_subidx}] UE{u} Ping: Loss={loss_pct:.1f}%, RTT={rtt_ms:.1f}ms -> {'OK' if ok else 'FAIL'}"))
            if not ok:
                passed = False
            step_subidx += 1
    else:
        total_mbps = sum(measured_mbps.get(u, 0.0) for u in sc.active_ues)
        total_active_prbs = sum(slice_prb_stats.get(u, {}).get("avg_prbs", 0.0) for u in sc.active_ues)
        step_subidx = 1

        # Evaluate active UEs with multi-criteria (PRB allocation and throughput delivery)
        for u in sc.active_ues:
            u_mbps = measured_mbps.get(u, 0.0)
            tput_share = (u_mbps / total_mbps * 100.0) if total_mbps > 0 else 0.0
            
            st_prb = slice_prb_stats.get(u, {})
            avg_prb = st_prb.get("avg_prbs", 0.0)
            avg_pct = st_prb.get("avg_pct", 0.0)
            prb_share = (avg_prb / total_active_prbs * 100.0) if total_active_prbs > 0 else avg_pct
            
            exp_min, exp_max = sc.expected_shares.get(u, (0.0, 100.0))
            
            # Slicing compliance: PRB allocation meets slice bounds or throughput share is within target
            tput_ok = (exp_min <= tput_share <= exp_max) and (u_mbps > 0.0)
            prb_ok = (exp_min <= prb_share <= exp_max) or (exp_min <= avg_pct <= exp_max)
            if not prb_ok and st_prb:
                if sc.name.startswith("dedicated_"):
                    prb_ok = (avg_pct >= (st_prb.get("dedicated_pct", 0.0) - 2.0))
                elif sc.name.startswith("min_"):
                    prb_ok = (avg_pct >= (st_prb.get("min_pct", 0.0) - 2.0))
                elif sc.name.startswith("max_"):
                    prb_ok = (avg_pct <= (st_prb.get("max_pct", 100.0) + 3.0))

            prb_tick = "\033[1;32m[✓]\033[0m" if prb_ok else "\033[1;31m[✗]\033[0m"
            tput_tick = "\033[1;32m[✓]\033[0m" if tput_ok else "\033[1;31m[✗]\033[0m"
            
            ok = (tput_ok or prb_ok) and (u_mbps > 0.0)
            status = "\033[1;32m[OK ✓]\033[0m" if ok else "\033[1;31m[FAIL ✗]\033[0m"
            
            msg = (
                f"  │ [5.{step_subidx}] UE{u} (Slice {u}): PRB: {avg_prb:4.1f} ({prb_share:4.1f}%, Exp: {exp_min:.0f}%-{exp_max:.0f}%) {prb_tick} │ "
                f"Tput: {u_mbps:4.1f} Mbps ({tput_share:4.1f}%, Exp: {exp_min:.0f}%-{exp_max:.0f}%) {tput_tick} -> {status}"
            )
            eval_lines.append((
                msg,
                f"[5.{step_subidx}] UE{u} Share: PRB={avg_prb:.1f} ({prb_share:.1f}%, Exp: {exp_min:.0f}%-{exp_max:.0f}%) {'[✓]' if prb_ok else '[✗]'} | "
                f"Throughput={u_mbps:.1f} Mbps ({tput_share:.1f}%, Exp: {exp_min:.0f}%-{exp_max:.0f}%) {'[✓]' if tput_ok else '[✗]'} -> {'OK' if ok else 'FAIL'}"
            ))
            if not ok:
                passed = False
            step_subidx += 1

        # Evaluate idle UEs
        for u in sc.idle_ues:
            u_mbps = measured_mbps.get(u, 0.0)
            st_prb = slice_prb_stats.get(u, {})
            avg_prb = st_prb.get("avg_prbs", 0.0)
            ok = (u_mbps < 0.5)
            status = "\033[1;32m[OK ✓]\033[0m" if ok else "\033[1;31m[FAIL ✗]\033[0m"
            msg = f"  │ [5.{step_subidx}] UE{u} (Slice {u}) Idle Isolation: [IDLE] (Allocated PRBs={avg_prb:.1f}) -> {status}"
            eval_lines.append((msg, f"[5.{step_subidx}] UE{u} Idle Isolation: {'OK' if ok else 'FAIL'}"))
            if not ok:
                passed = False
            step_subidx += 1

    if is_pf_scenario(sc):
        gnb_text = fetch_gnb_log_text(log_dir)
        mac_log = log_dir / "mac_stats.log"
        mac_file_text = ""
        if mac_log.is_file():
            try:
                mac_file_text = mac_log.read_text(errors="ignore")
            except Exception:
                pass
        has_slice = mac_stats_has_ns_slice_log(gnb_text) or mac_stats_has_ns_slice_log(mac_file_text)
        ok = not has_slice
        status = "\033[1;32m[OK ✓]\033[0m" if ok else "\033[1;31m[FAIL ✗]\033[0m"
        msg = (
            f"  │ [5.{step_subidx}] PF scheduler: NS UL/DL slice PRB log must be absent "
            f"(no 'NS PRB allocation' / dedicated/min/max) -> {status}"
        )
        eval_lines.append((
            msg,
            f"[5.{step_subidx}] PF no NS slice log: {'OK (absent)' if ok else 'FAIL (NS slice log present)'}",
        ))
        if not ok:
            passed = False

    box_width = 110
    print(f"  \033[1;36m┌{'─' * (box_width - 4)}┐\033[0m")
    with open(eval_file, "w") as f:
        f.write(f"EVALUATION FOR: {sc.name}\n\n")
        if traffic_cmd_lines:
            f.write("Traffic commands:\n")
            for line in traffic_cmd_lines:
                f.write(f"  {line}\n")
            f.write("\n")
        if gnb_plain:
            f.write("gNB statistics:\n")
            for line in gnb_plain:
                f.write(f"  {line}\n")
            f.write("\n")
        for console_line, log_line in eval_lines:
            # Strip ANSI when measuring plain length for right padding
            plain_len = len(re.sub(r"\x1b\[[0-9;]*m", "", console_line))
            pad = max(0, box_width - plain_len - 2)
            print(f"{console_line}{' ' * pad}\033[1;36m│\033[0m")
            f.write(log_line + "\n")
        print(f"  \033[1;36m└{'─' * (box_width - 4)}┘\033[0m")
        verdict_text = "PASSED" if passed else "FAILED"
        f.write(f"\nFINAL VERDICT: {verdict_text}\n")

    report_abs_path = (eval_file).resolve()
    summary_abs_path = (log_dir / "test_summary.log").resolve()
    log_dir_abs_path = log_dir.resolve()

    with open(eval_file, "a") as f:
        f.write(f"Report File:  {report_abs_path}\n")
        f.write(f"Summary File: {summary_abs_path}\n")
        f.write(f"Log Dir:      {log_dir_abs_path}\n")
        f.write("========================================================================\n")

    with open(log_dir / "test_summary.log", "a") as f:
        f.write(f"\nFINAL VERDICT: {verdict_text}\n")
        f.write(f"Report File:   {report_abs_path}\n")
        f.write(f"Summary File:  {summary_abs_path}\n")
        f.write(f"Log Dir:       {log_dir_abs_path}\n")
        f.write("========================================================================\n")

    print("  \033[1;36m└─────────────────────────────────────────────────────────────────────────┘\033[0m")

    # Final Verdict & Absolute Report Paths
    verdict_str = "\033[1;32mPASSED [✓]\033[0m" if passed else "\033[1;31mFAILED [✗]\033[0m"
    print("------------------------------------------------------------------------")
    print(f"  FINAL VERDICT:          {verdict_str}")
    print(f"  Final Report (Abs Path): \033[1;33m{report_abs_path}\033[0m")
    print(f"  Test Summary (Abs Path): \033[1;33m{summary_abs_path}\033[0m")
    print(f"  Log Directory:           \033[1;36m{log_dir_abs_path}\033[0m")
    print("========================================================================\n")
    return passed, report_abs_path


def launch_tmux_session(
    sc: TestScenario,
    direction: str,
    duration: int,
    attach: bool = False,
    no_attach: bool = False,
    run_dir: Optional[Path] = None,
    test_idx: Optional[str] = None,
    slice_config_mode: str = SLICE_CONFIG_STARTUP,
) -> tuple[bool, Path]:
    """Launch the 2-tab tmux visual testing session (servers & clients) with dedicated logging."""
    if not shutil.which("tmux"):
        print("[ERROR] tmux is not installed. Please install tmux (sudo apt install tmux).", file=sys.stderr)
        sys.exit(1)

    log_dir = setup_test_logging(
        sc, direction, run_dir=run_dir, test_idx=test_idx, slice_config_mode=slice_config_mode
    )
    mac_log = log_dir / "mac_stats.log"
    ue1_log = log_dir / "ue1_traffic.log"
    ue2_log = log_dir / "ue2_traffic.log"

    method = SLICE_CONFIG_LABELS.get(slice_config_mode, slice_config_mode)
    print(f"\n========================================================================")
    print(f"  LAUNCHING LIVE TMUX SESSION: {sc.title}")
    print(f"========================================================================")
    print(f"Scenario:     {sc.description}")
    print(f"Slice config: {method}")
    print(f"Active UEs:   {sc.active_ues} | Idle UEs: {sc.idle_ues}")
    print(f"Log Directory: {log_dir}")
    print("------------------------------------------------------------------------")

    if slice_config_mode == SLICE_CONFIG_E2AP:
        if is_pf_scenario(sc):
            print("  -> PF test: no xApp slice SET. Expect no NS UL/DL slice PRB log.")
            if not wait_no_ns_slice_log(log_dir):
                eval_file = log_dir / "evaluation.log"
                eval_file.write_text(
                    "========================================================================\n"
                    f"  TEST {sc.name}\n"
                    "  RESULT: FAILED [✗]\n"
                    "  REASON: PF scheduler (000) must not emit NS UL/DL slice PRB allocation logs.\n"
                    "========================================================================\n"
                )
                return False, eval_file
        else:
            rules = e2ap_rules_for(sc)
            if not apply_xapp_policy(rules, log_dir, required=True):
                eval_file = log_dir / "evaluation.log"
                eval_file.write_text(
                    "========================================================================\n"
                    f"  TEST {sc.name}\n"
                    "  RESULT: FAILED [✗]\n"
                    "  REASON: Failed to apply slice policy via FlexRIC xApp E2AP REST.\n"
                    "========================================================================\n"
                )
                return False, eval_file
            if not wait_ns_prb_policy_applied(rules, log_dir):
                eval_file = log_dir / "evaluation.log"
                eval_file.write_text(
                    "========================================================================\n"
                    f"  TEST {sc.name}\n"
                    "  RESULT: FAILED [✗]\n"
                    "  REASON: gNB mac stats NS UL/DL dedicated/min/max did not match xApp SET.\n"
                    "========================================================================\n"
                )
                return False, eval_file
    else:
        print("  -> Slice policy from gNB startup YAML (bringup --scenario). xApp E2 not used.")
        with open(log_dir / "startup_slice_policy.txt", "w") as f:
            f.write("Applied via gNB YAML at bringup (--scenario). xApp E2 not used.\n")
            for r in sc.rules:
                f.write(
                    f"  SST={r.sst} SD={r.sd} ded={r.dedicated} min={r.min_ratio} max={r.max_ratio}\n"
                )
        if is_pf_scenario(sc):
            print("  -> PF test (startup YAML, sch=PF): expect no NS UL/DL slice PRB log.")
            if not wait_no_ns_slice_log(log_dir):
                eval_file = log_dir / "evaluation.log"
                eval_file.write_text(
                    "========================================================================\n"
                    f"  TEST {sc.name}\n"
                    "  RESULT: FAILED [✗]\n"
                    "  REASON: PF scheduler (000) must not emit NS UL/DL slice PRB allocation logs.\n"
                    "========================================================================\n"
                )
                return False, eval_file

    # Step 2: Ensure persistent UPF server receivers are running cleanly
    setup_iperf_servers(direction)

    # 1. PANE 0 Script: Live MAC Stats & Monitor
    p0_script = log_dir / "pane0_monitor.sh"
    with open(p0_script, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("while true; do\n")
        f.write("  clear\n")
        f.write(f"  echo -e '\\033[1;36m================================================================================\\033[0m'\n")
        f.write(f"  echo -e '\\033[1;33m       [LIVE 5G SLICING TESTBED MONITOR: {sc.title}]\\033[0m'\n")
        f.write(f"  echo -e '\\033[1;36m================================================================================\\033[0m'\n")
        f.write(f"  echo -e '\\033[1;37mStatus: \\033[0m{sc.description}'\n")
        f.write(f"  echo -e '\\033[1;32mKey Takeaway: \\033[0m{sc.explanation}'\n")
        f.write(f"  echo -e '\\033[1;35mLogs saved to: \\033[0m{log_dir}'\n")
        f.write("  echo -e '\\033[1;34m[Real-time MAC & Slicing Stats (gNB Telnet Port 19090/9090)]:\\033[0m'\n")
        f.write(f"  (echo -e \"\\n--- [$(date +\"%Y-%m-%d %H:%M:%S\")] ---\"; (echo \"mac stats\"; sleep 0.8) | telnet 127.0.0.1 19090 2>/dev/null || (echo \"mac stats\"; sleep 0.8) | telnet 127.0.0.1 9090 2>/dev/null) | grep -E \"NS|slice SST|UE|PRB\" | tee -a {mac_log}\n")
        f.write("  echo '--------------------------------------------------------------------------------'\n")
        f.write("  echo '[Press Ctrl-C or close window to finish test]'\n")
        f.write("  sleep 1\n")
        f.write("done\n")
    p0_script.chmod(0o755)

    is_ping_test = getattr(sc, "test_mode", "iperf") == "ping"
    dir_val = getattr(sc, "direction", direction or "ul")
    traffic = get_traffic_params(sc, direction)
    proto_flag = traffic["proto_flag"]
    rev_flag = traffic["rev_flag"]
    proto_val = traffic["proto"]

    with open(log_dir / "test_summary.log", "a") as f:
        f.write("\nTraffic commands:\n")
        for u in sc.active_ues:
            f.write(f"  {format_traffic_command(u, sc, duration, direction)}\n")
        f.write("========================================================================\n")

    # 2. Generate Pane Scripts for all 5 UEs (tail live client logs)
    ue_scripts = {}
    for u in range(1, 6):
        u_script = log_dir / f"pane_ue{u}.sh"
        u_log = log_dir / f"ue{u}_traffic.log"
        port = 5200 + u
        sd_hex = f"0x00000{u}"
        with open(u_script, "w") as f:
            f.write("#!/bin/bash\n")
            if u in sc.active_ues:
                f.write("echo -e '\\033[1;32m================================================\\033[0m'\n")
                f.write(f"echo -e '\\033[1;32m  UE {u} (Slice {u}, SST=1, SD={sd_hex}) [{dir_val.upper()} {proto_val.upper()}]  \\033[0m'\n")
                f.write("echo -e '\\033[1;32m================================================\\033[0m'\n")
                f.write(f"touch {u_log}\n")
                f.write(f"exec tail -n 50 -f {u_log}\n")
            else:
                f.write("echo -e '\\033[1;31m================================================\\033[0m'\n")
                f.write(f"echo -e '\\033[1;31m  UE {u} (Slice {u}, SST=1, SD={sd_hex}) [IDLE]    \\033[0m'\n")
                f.write("echo -e '\\033[1;31m================================================\\033[0m'\n")
                f.write(f"echo \"[$(date +\"%Y-%m-%d %H:%M:%S\")] UE {u} is IDLE (0 traffic) to observe PRB isolation/sharing.\" | tee -a {u_log}\n")
                f.write(f"touch {u_log}\n")
                f.write(f"exec tail -n 50 -f {u_log}\n")
        u_script.chmod(0o755)
        ue_scripts[u] = u_script

    # 3. Generate Pane Scripts for all 5 UPF Servers (tail live server logs)
    server_scripts = {}
    for u in range(1, 6):
        s_script = log_dir / f"pane_server{u}.sh"
        port = 5200 + u
        with open(s_script, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("echo -e '\\033[1;36m================================================\\033[0m'\n")
            f.write(f"echo -e '\\033[1;36m  UPF Server Port {port} (UE {u} Receiver) \\033[0m'\n")
            f.write("echo -e '\\033[1;36m================================================\\033[0m'\n")
            f.write(f"docker exec nws-5gc touch /tmp/iperf_server_{u}.log\n")
            f.write(f"exec docker exec nws-5gc tail -n 50 -f /tmp/iperf_server_{u}.log\n")
        s_script.chmod(0o755)
        server_scripts[u] = s_script

    # 4. Setup or refresh tmux session tabs via TmuxManager
    tmux = TmuxManager(TMUX_SESSION)
    tmux.setup_test_panes(server_scripts, ue_scripts)

    traffic_procs = []
    log_procs = []
    log_files = []

    # 5. Launch all active UE traffic processes deterministically in parallel
    for u in sc.active_ues:
        u_log_path = log_dir / f"ue{u}_traffic.log"
        f = open(u_log_path, "w")
        log_files.append(f)
        f.write(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting {dir_val.upper()} {proto_val.upper()} burst ({duration}s)...\n")
        f.flush()

        ue_ip = f"10.45.0.{30 + u}"
        if is_ping_test:
            cmd = ["docker", "exec", f"nws-oai-nr-ue{u}", "ping", "-I", ue_ip, "-c", str(duration), "-i", "1", UPF_IP]
        else:
            cmd = ["docker", "exec", f"nws-oai-nr-ue{u}", "iperf3", "-c", UPF_IP, "-B", ue_ip, "-p", str(5200 + u)]
            if rev_flag:
                cmd.append(rev_flag)
            if proto_flag:
                cmd.extend(proto_flag.split())
            cmd.extend(["--connect-timeout", "5000", "--forceflush", "-i", "1", "-t", str(duration)])

        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
        traffic_procs.append(p)

    # 6. Stream gNB and UE container logs to dedicated log files in background

    # gNB log streaming
    gnb_file = open(log_dir / "gnb_container.log", "w")
    log_files.append(gnb_file)
    log_procs.append(
        subprocess.Popen(
            ["docker", "logs", "-f", "--tail", "100", GNB_CONTAINER],
            stdout=gnb_file,
            stderr=subprocess.STDOUT,
        )
    )

    # UEs log streaming
    for ue_idx in range(1, 6):
        ue_file = open(log_dir / f"ue{ue_idx}_container.log", "w")
        log_files.append(ue_file)
        log_procs.append(
            subprocess.Popen(
                ["docker", "logs", "-f", "--tail", "100", f"nws-oai-nr-ue{ue_idx}"],
                stdout=ue_file,
                stderr=subprocess.STDOUT,
            )
        )

    try:
        measured = sample_live_traffic_countdown(sc, dir_val, duration, mac_log, log_dir)
        passed, report_path = evaluate_test_results(sc, measured, log_dir, duration=duration, direction=dir_val)
        return passed, report_path
    finally:
        for p in traffic_procs:
            try:
                p.terminate()
                p.wait(timeout=1)
            except Exception:
                pass
        for p in log_procs:
            try:
                p.terminate()
                p.wait(timeout=1)
            except Exception:
                pass
        for f in log_files:
            try:
                f.close()
            except Exception:
                pass

        for u in range(1, 6):
            srv_content = run_cmd(["docker", "exec", CORE_CONTAINER, "cat", f"/tmp/iperf_server_{u}.log"]).stdout
            if srv_content:
                try:
                    (log_dir / f"server_ue{u}_iperf.log").write_text(srv_content)
                except Exception:
                    pass

        # Terminate running test processes cleanly without killing the interactive tmux session
        run_cmd(["docker", "exec", CORE_CONTAINER, "pkill", "-9", "-f", "iperf3|ping"])
        for ue in range(1, 6):
            run_cmd(["docker", "exec", f"nws-oai-nr-ue{ue}", "pkill", "-9", "-f", "iperf3|ping"])


NUMERIC_ALIASES = {
    # 000: Pure Proportional Fair (sch=PF, no slicing algorithm)
    "000": "pf_only",
    "001": "pf_dl_udp",
    "002": "pf_dl_tcp",
    "003": "pf_ul_udp",
    "004": "pf_ul_tcp",
    "0": "pf_only",
    "1": "pf_dl_udp",
    "2": "pf_dl_tcp",
    "3": "pf_ul_udp",
    "4": "pf_ul_tcp",

    # 100: As-no-slice policy (0/0/100% under NSBOTH)
    "100": "as_no_slice",
    "101": "as_no_slice_dl_udp",
    "102": "as_no_slice_dl_tcp",
    "103": "as_no_slice_ul_udp",
    "104": "as_no_slice_ul_tcp",

    # 200: Dedicated Policy
    # 201-204: Dedicated Sym Idle
    "201": "dedicated_sym_idle_dl_udp",
    "202": "dedicated_sym_idle_dl_tcp",
    "203": "dedicated_sym_idle_ul_udp",
    "204": "dedicated_sym_idle_ul_tcp",
    # 205-208: Dedicated Sym Full
    "205": "dedicated_sym_full_dl_udp",
    "206": "dedicated_sym_full_dl_tcp",
    "207": "dedicated_sym_full_ul_udp",
    "208": "dedicated_sym_full_ul_tcp",
    # 211-214: Dedicated Asym Idle
    "211": "dedicated_asym_idle_dl_udp",
    "212": "dedicated_asym_idle_dl_tcp",
    "213": "dedicated_asym_idle_ul_udp",
    "214": "dedicated_asym_idle_ul_tcp",
    # 215-218: Dedicated Asym Full
    "215": "dedicated_asym_full_dl_udp",
    "216": "dedicated_asym_full_dl_tcp",
    "217": "dedicated_asym_full_ul_udp",
    "218": "dedicated_asym_full_ul_tcp",

    # 300: Min Policy
    # 301-304: Min Sym Idle
    "301": "min_sym_idle_dl_udp",
    "302": "min_sym_idle_dl_tcp",
    "303": "min_sym_idle_ul_udp",
    "304": "min_sym_idle_ul_tcp",
    # 305-308: Min Sym Full
    "305": "min_sym_full_dl_udp",
    "306": "min_sym_full_dl_tcp",
    "307": "min_sym_full_ul_udp",
    "308": "min_sym_full_ul_tcp",
    # 311-314: Min Asym Idle
    "311": "min_asym_idle_dl_udp",
    "312": "min_asym_idle_dl_tcp",
    "313": "min_asym_idle_ul_udp",
    "314": "min_asym_idle_ul_tcp",
    # 315-318: Min Asym Full
    "315": "min_asym_full_dl_udp",
    "316": "min_asym_full_dl_tcp",
    "317": "min_asym_full_ul_udp",
    "318": "min_asym_full_ul_tcp",

    # 400: Max Policy
    # 401-404: Max Sym Idle
    "401": "max_sym_idle_dl_udp",
    "402": "max_sym_idle_dl_tcp",
    "403": "max_sym_idle_ul_udp",
    "404": "max_sym_idle_ul_tcp",
    # 405-408: Max Sym Full
    "405": "max_sym_full_dl_udp",
    "406": "max_sym_full_dl_tcp",
    "407": "max_sym_full_ul_udp",
    "408": "max_sym_full_ul_tcp",
    # 411-414: Max Asym Idle
    "411": "max_asym_idle_dl_udp",
    "412": "max_asym_idle_dl_tcp",
    "413": "max_asym_idle_ul_udp",
    "414": "max_asym_idle_ul_tcp",
    # 415-418: Max Asym Full
    "415": "max_asym_full_dl_udp",
    "416": "max_asym_full_dl_tcp",
    "417": "max_asym_full_ul_udp",
    "418": "max_asym_full_ul_tcp",
}
for _k, _v in list(NUMERIC_ALIASES.items()):
    if _v in SCENARIOS:
        SCENARIOS[_k] = SCENARIOS[_v]


def parse_test_arguments(test_args: list[str]) -> list[str]:
    """Expand user test inputs (e.g. ['101', '102-104', '201,205', 'all']) into sorted unique test keys."""
    expanded = []
    for arg in test_args:
        tokens = arg.replace(",", " ").split()
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if tok.lower() == "all":
                return [k for k in sorted(NUMERIC_ALIASES.keys(), key=lambda x: int(x)) if int(k) >= 100]
            if "-" in tok:
                parts = tok.split("-")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    s_val, e_val = int(parts[0]), int(parts[1])
                    for num in range(s_val, e_val + 1):
                        s_str = str(num)
                        if s_str in SCENARIOS or s_str in NUMERIC_ALIASES:
                            expanded.append(s_str)
                    continue
            if tok in SCENARIOS or tok in NUMERIC_ALIASES:
                expanded.append(tok)
    return expanded or ["as_no_slice"]


def print_batch_summary_report(batch_results: list[tuple[str, str, bool, Path]], run_dir: Optional[Path] = None):
    """Print consolidated summary table for batch test runs with clickable absolute report links."""
    print("\n========================================================================")
    print("                      BATCH EXECUTION SUMMARY REPORT")
    print("========================================================================")
    passed_cnt = 0
    for idx, (t_key, s_name, passed, rep_path) in enumerate(batch_results, 1):
        status_str = "\033[1;32mPASSED [✓]\033[0m" if passed else "\033[1;31mFAILED [✗]\033[0m"
        if passed:
            passed_cnt += 1
        abs_uri = Path(rep_path).resolve()
        print(f"  [{idx:2d}] Test {t_key:<4} ({s_name:<30}): {status_str}")
        print(f"       \033[1;34mDetailed Report:\033[0m {abs_uri}")
    print("------------------------------------------------------------------------")
    total_cnt = len(batch_results)
    rate_pct = (passed_cnt * 100 // total_cnt) if total_cnt > 0 else 0
    print(f"  TOTAL EXECUTED: {total_cnt} | PASSED: {passed_cnt} | FAILED: {total_cnt - passed_cnt} | SUCCESS RATE: {passed_cnt}/{total_cnt} ({rate_pct}%)")
    if run_dir:
        print(f"  RUN DIRECTORY:  \033[1;36m{run_dir.resolve()}\033[0m")
    print("========================================================================\n\n")

    # Save run_summary.log inside the dedicated run folder
    if run_dir:
        summary_lines = [
            "========================================================================",
            "                      BATCH EXECUTION SUMMARY REPORT",
            "========================================================================",
        ]
        for idx, (t_key, s_name, passed, rep_path) in enumerate(batch_results, 1):
            st = "PASSED [OK]" if passed else "FAILED [FAIL]"
            summary_lines.append(f"  [{idx:2d}] Test {t_key:<4} ({s_name:<30}): {st}")
            summary_lines.append(f"       Detailed Report: {Path(rep_path).resolve()}")
        summary_lines.append("------------------------------------------------------------------------")
        summary_lines.append(f"  TOTAL EXECUTED: {total_cnt} | PASSED: {passed_cnt} | FAILED: {total_cnt - passed_cnt} | SUCCESS RATE: {passed_cnt}/{total_cnt} ({rate_pct}%)")
        summary_lines.append(f"  RUN DIRECTORY:  {run_dir.resolve()}")
        summary_lines.append("========================================================================\n")
        try:
            (run_dir / "run_summary.log").write_text("\n".join(summary_lines))
        except Exception:
            pass


def run_batch_tests(
    test_keys: list[str],
    duration: int = 30,
    skip_prep: bool = False,
    force_restart: bool = False,
    attach: bool = False,
    no_attach: bool = False,
    dir_override: Optional[str] = None,
    proto_override: Optional[str] = None,
    bitrate: Optional[str] = None,
    streams: int = 5,
    slice_config_mode: str = SLICE_CONFIG_STARTUP,
) -> list[tuple[str, str, bool, Path]]:
    """Execute a list of scenario keys sequentially within a dedicated timestamped run folder."""
    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = LOGS_DIR / f"run_{run_ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Update latest symlink to point to this dedicated run directory
    latest_link = LOGS_DIR / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    try:
        latest_link.symlink_to(run_dir.relative_to(LOGS_DIR))
    except Exception:
        pass

    batch_results = []
    for idx, test_key in enumerate(test_keys, 1):
        if test_key not in SCENARIOS:
            print(f"\033[1;31m[ERROR] Unknown test scenario '{test_key}'. Skipping.\033[0m")
            continue
        scenario = SCENARIOS[test_key]
        if dir_override:
            scenario.direction = dir_override
        if proto_override:
            scenario.protocol = proto_override
        if bitrate:
            scenario.bitrate = bitrate
        scenario.streams = streams

        if len(test_keys) > 1:
            print(f"\n\033[1;35m========================================================================")
            print(f"  >>> EXECUTING BATCH TEST [{idx}/{len(test_keys)}]: #{test_key} - {scenario.name} <<<")
            print(f"========================================================================\033[0m")

        # Start of a run: always restart gNB/UEs.
        # Later cases: startup YAML restarts only if the slice group changed;
        # E2AP NSBOTH cases do not restart (xApp SET only). PF (000) uses sch=PF
        # and restarts when switching to/from NSBOTH (state key e2ap:pf vs e2ap:nsboth).
        if slice_config_mode == SLICE_CONFIG_E2AP:
            should_restart = idx == 1
        else:
            should_restart = idx == 1 or force_restart
        if not skip_prep:
            if not auto_prepare_testbed(
                num_ues=5,
                scenario_name=scenario.name,
                force_restart=should_restart,
                slice_config_mode=slice_config_mode,
            ):
                print(f"[ERROR] Auto-preparation failed for test {test_key} ({scenario.name}). Skipping test execution.")
                test_dir = run_dir / f"{test_key}_{scenario.name}"
                test_dir.mkdir(parents=True, exist_ok=True)
                eval_log = test_dir / "evaluation.log"
                eval_log.write_text(f"========================================================================\n"
                                   f"  TEST #{test_key} - {scenario.name}\n"
                                   f"  RESULT: FAILED [✗]\n"
                                   f"  REASON: Initial testbed conditions not met (PDU session or bringup failed).\n"
                                   f"========================================================================\n")
                batch_results.append((test_key, scenario.name, False, eval_log))
                continue

        passed, report_path = launch_tmux_session(
            scenario,
            scenario.direction,
            duration,
            attach=attach,
            no_attach=no_attach,
            run_dir=run_dir,
            test_idx=str(test_key),
            slice_config_mode=slice_config_mode,
        )
        batch_results.append((test_key, scenario.name, passed, report_path))

    if len(test_keys) > 1:
        print_batch_summary_report(batch_results, run_dir=run_dir)
    return batch_results


def display_interactive_menu(slice_config_mode: str = SLICE_CONFIG_STARTUP):
    """Display interactive CLI test suite menu directly within Tab 0 (console)."""
    method = SLICE_CONFIG_LABELS.get(slice_config_mode, slice_config_mode)
    while True:
        os.system("clear")
        print("\033[1;36m========================================================================\033[0m")
        print("\033[1;33m       3GPP 5G Network Slicing Live rfsim Test Suite (PR #451)         \033[0m")
        print(f"\033[1;36m       Slice config: {method}\033[0m")
        print("\033[1;36m========================================================================\033[0m")
        print("Select test scenario(s) to launch live inside single tmux environment:\n")
        print("  \033[1;35m[000. NO SLICE PF (True Pure Proportional Fair, sch=PF, no slice layer)]\033[0m")
        print("   000) NO SLICE PF PING     -> 5 UEs Ping 30s to UPF Core (sch=PF)")
        print("   001) DL UDP   002) DL TCP   003) UL UDP   004) UL TCP   (Pure PF Full Load)\n")
        print("  \033[1;32m[100. AS-NO-SLICE (0/0/100% under NSBOTH — not PF)]\033[0m")
        print("   100) AS-NO-SLICE PING     -> 5 UEs Ping 30s to UPF Core")
        print("   101) DL UDP   102) DL TCP   103) UL UDP   104) UL TCP   (Equal Fair Share Full Load)\n")
        print("  \033[1;32m[200. DEDICATED POLICY (Pass 1 - Fixed Non-Shareable Reservation)]\033[0m")
        print("   --- SYMMETRIC IDLE (15/15/15/15/15% -> UE1 active, UEs 2-5 idle, 60% held) ---")
        print("   201) DL UDP   202) DL TCP   203) UL UDP   204) UL TCP")
        print("   --- SYMMETRIC FULL (15/15/15/15/15% -> UEs 1-5 active, equal ~20% share) ---")
        print("   205) DL UDP   206) DL TCP   207) UL UDP   208) UL TCP")
        print("   --- ASYMMETRIC IDLE (15/15/15/15/7% -> UE1 active, UEs 2-5 idle, 52% held) ---")
        print("   211) DL UDP   212) DL TCP   213) UL UDP   214) UL TCP")
        print("   --- ASYMMETRIC FULL (15/15/15/15/7% -> UEs 1-5 active, UE5 gets 7% window) ---")
        print("   215) DL UDP   216) DL TCP   217) UL UDP   218) UL TCP\n")
        print("  \033[1;32m[300. MIN POLICY (Pass 2 - Prioritized Shareable Guarantee)]\033[0m")
        print("   --- SYMMETRIC IDLE (20/20/20/20/20% -> UE1 active, UEs 2-5 idle, bursts 100%) ---")
        print("   301) DL UDP   302) DL TCP   303) UL UDP   304) UL TCP")
        print("   --- SYMMETRIC FULL (20/20/20/20/20% -> UEs 1-5 active, equal ~20% share) ---")
        print("   305) DL UDP   306) DL TCP   307) UL UDP   308) UL TCP")
        print("   --- ASYMMETRIC IDLE (20/20/20/20/10% -> UE1 active, UEs 2-5 idle, bursts 100%) ---")
        print("   311) DL UDP   312) DL TCP   313) UL UDP   314) UL TCP")
        print("   --- ASYMMETRIC FULL (20/20/20/20/10% -> UEs 1-5 active, UE5 gets ~half share) ---")
        print("   315) DL UDP   316) DL TCP   317) UL UDP   318) UL TCP\n")
        print("  \033[1;32m[400. MAX POLICY (Pass 3 - Hard Ceiling Caps)]\033[0m")
        print("   --- SYMMETRIC IDLE (100/100/100/100/100% -> UE1 active, bursts 100%) ---")
        print("   401) DL UDP   402) DL TCP   403) UL UDP   404) UL TCP")
        print("   --- SYMMETRIC FULL (100/100/100/100/100% -> UEs 1-5 active, equal fair share) ---")
        print("   405) DL UDP   406) DL TCP   407) UL UDP   408) UL TCP")
        print("   --- ASYMMETRIC IDLE (100/100/100/50/50% -> UE5 active, capped at 50% max) ---")
        print("   411) DL UDP   412) DL TCP   413) UL UDP   414) UL TCP")
        print("   --- ASYMMETRIC FULL (100/100/100/50/50% -> UEs 1-5 active, UEs 4-5 capped) ---")
        print("   415) DL UDP   416) DL TCP   417) UL UDP   418) UL TCP")
        print("\033[1;36m========================================================================\033[0m")
        print("Enter test case(s) (e.g. '101', '101 102 103 104', '101-104', '201 205', 'all', or 'q'):")
        try:
            choice = input("Selection: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting menu.")
            break

        if not choice:
            continue
        if choice.lower() in ("q", "quit", "exit"):
            break

        tests_to_run = parse_test_arguments(choice.split())
        run_batch_tests(
            tests_to_run,
            duration=30,
            skip_prep=False,
            force_restart=False,
            slice_config_mode=slice_config_mode,
        )

        print("\n\033[1;33mExecution finished. Press Enter to return to menu...\033[0m", end="")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            break


def run_cli(slice_config_mode: str = SLICE_CONFIG_STARTUP):
    """CLI entry used by test.py (startup YAML) and test_with_e2ap.py (xApp E2AP)."""
    method = SLICE_CONFIG_LABELS.get(slice_config_mode, slice_config_mode)
    # If not inside tmux and running interactively in terminal, hand over to visual tmux session
    skip_tmux = any(a in ("--no-attach", "--help", "-h", "--undeploy", "-u") for a in sys.argv)
    if "TMUX" not in os.environ and sys.stdin.isatty() and not skip_tmux:
        TmuxManager.launch_in_tmux(sys.argv)
        return

    # If inside tmux, apply session styling via TmuxManager
    if "TMUX" in os.environ:
        TmuxManager(TMUX_SESSION).apply_styling()

    # If invoked directly with no CLI arguments, default to interactive menu mode
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] == "--menu"):
        display_interactive_menu(slice_config_mode)
        return

    parser = argparse.ArgumentParser(
        description=(
            "Live 3GPP 5G rfsim Slicing Test Matrix (PR #451). "
            f"Slice config method: {method}."
        )
    )
    parser.add_argument(
        "--test",
        nargs="+",
        default=["as_no_slice"],
        help="Slicing scenario name(s) or index(es) (e.g. 100, 101 102 103, 201-204, all, etc.)",
    )
    parser.add_argument("--menu", action="store_true", help="Launch interactive menu inside single tmux environment")
    parser.add_argument("--dir", choices=["ul", "dl"], default=None, help="Override traffic direction (default: scenario setting or ul)")
    parser.add_argument("--proto", choices=["udp", "tcp"], default=None, help="Override traffic protocol (default: scenario setting or udp)")
    parser.add_argument("--time", type=int, default=30, help="Traffic burst duration per test in seconds (default: 30s)")
    parser.add_argument("--bitrate", default=None, help="Target bitrate for UDP traffic per stream (default: 10M for UL, 20M for DL)")
    parser.add_argument("--streams", "-P", type=int, default=5, help="Number of parallel streams per UE (default: 5)")
    parser.add_argument("--skip-prep", action="store_true", help="Skip automated 5G testbed health check and preparation")
    parser.add_argument(
        "-u",
        "--undeploy",
        action="store_true",
        help="Undeploy 5GC, RAN (gNB), and UE containers and exit (bringup.py down --with-core)",
    )
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help=(
            "Restart gNB/UEs on the first test of this run (always happens) and, for "
            "startup YAML, also when a later case shares the same slice group. "
            "E2AP later cases never restart; they only SET policy via xApp."
        ),
    )
    parser.add_argument("--attach", action="store_true", default=False, help="Explicitly attach to interactive tmux session")
    parser.add_argument("--no-attach", action="store_true", default=False, help="Run headless without attaching to tmux")
    args = parser.parse_args()

    if args.undeploy:
        sys.exit(undeploy_testbed())

    if args.menu:
        display_interactive_menu(slice_config_mode)
        return

    tests_to_run = parse_test_arguments(args.test)
    run_batch_tests(
        tests_to_run,
        duration=args.time,
        skip_prep=args.skip_prep,
        force_restart=args.force_restart,
        attach=args.attach,
        no_attach=args.no_attach,
        dir_override=args.dir,
        proto_override=args.proto,
        bitrate=args.bitrate,
        streams=args.streams,
        slice_config_mode=slice_config_mode,
    )


def cli_entry(slice_config_mode: str = SLICE_CONFIG_STARTUP) -> None:
    """Process entrypoint with crash logging. Used by test.py and test_with_e2ap.py."""
    try:
        run_cli(slice_config_mode)
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"\n\033[1;31m[CRITICAL ERROR] Runner crashed with unhandled exception:\n{err_msg}\033[0m")
        try:
            with open("/tmp/test_runner_debug.log", "a") as f:
                f.write(f"\n[{datetime.datetime.now()}] CRASH:\n{err_msg}\n")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    cli_entry(SLICE_CONFIG_STARTUP)

#!/usr/bin/env python3
"""
3GPP Network Slicing Live rfsim Test Suite — FlexRIC xApp E2AP slice config.

Each run restarts gNB and UEs once at NSBOTH 0/0/100%. Later cases in the
same run do not restart: they only change slice policy via FlexRIC xApp
REST (`PUT /api/v1/slices`). Traffic starts after mac stats show the new
NS UL/DL dedicated/min/max.

Usage:
    python3 test_with_e2ap.py
    python3 test_with_e2ap.py --test 201
    python3 test_with_e2ap.py --test 201 205 301 --time 30
    python3 test_with_e2ap.py --undeploy
    python3 test_with_e2ap.py -u
"""

from test_common import SLICE_CONFIG_E2AP, cli_entry

if __name__ == "__main__":
    cli_entry(SLICE_CONFIG_E2AP)

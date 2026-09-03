#!/usr/bin/env python3
"""
3GPP Network Slicing Live rfsim Test Suite — FlexRIC xApp E2AP slice config.

First bringup uses NSBOTH with dedicated=0 / min=0 / max=100 on all five
S-NSSAIs. After that the gNB and UEs are never restarted: each test only
changes slice policy via FlexRIC xApp REST (`PUT /api/v1/slices`), which
issues E2 Slice SM CONTROL.

Usage:
  python3 test_with_e2ap.py
  python3 test_with_e2ap.py --test 201
  python3 test_with_e2ap.py --test 201 205 301 --time 30
"""

from test_common import SLICE_CONFIG_E2AP, cli_entry

if __name__ == "__main__":
    cli_entry(SLICE_CONFIG_E2AP)

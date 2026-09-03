#!/usr/bin/env python3
"""
3GPP Network Slicing Live rfsim Test Suite — gNB startup YAML slice config.

Slice dedicated/min/max ratios are applied by (re)starting the gNB with
scenario-specific Slices in the bringup YAML (`bringup.py --scenario`).
FlexRIC xApp / E2 CONTROL is not used.

Usage:
  python3 test.py
  python3 test.py --test 201
  python3 test.py --test 201 205 301 --time 30
"""

from test_common import SLICE_CONFIG_STARTUP, cli_entry

if __name__ == "__main__":
    cli_entry(SLICE_CONFIG_STARTUP)

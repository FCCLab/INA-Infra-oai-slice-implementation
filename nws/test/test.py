#!/usr/bin/env python3
"""
3GPP Network Slicing Live rfsim Test Suite — gNB startup YAML slice config.

Each run restarts gNB and UEs once. Later cases in the same run restart
only when the YAML slice group changes. Slice dedicated/min/max ratios
are applied via `bringup.py --scenario`. FlexRIC xApp / E2 is not used.

Usage:
  python3 test.py
  python3 test.py --test 201
  python3 test.py --test 201 205 301 --time 30
"""

from test_common import SLICE_CONFIG_STARTUP, cli_entry

if __name__ == "__main__":
    cli_entry(SLICE_CONFIG_STARTUP)

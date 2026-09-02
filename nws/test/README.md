# 3GPP Network Slicing PRB Allocation Test Suite (PR #451)

This directory contains test suites to validate the 3GPP Network Slicing PRB Allocation algorithm and end-to-end performance across the 6 core resource allocation dimensions:

| Test Scenario | Active / Idle Slices | PRB Configuration | Expected Behavior |
| :--- | :--- | :--- | :--- |
| **`dedicated_idle`** | Slice 1 active, Slice 2 idle | S1: Ded=30%, S2: Ded=30% | Slice 2 dedicated PRBs (30%) remain strictly reserved / non-shareable. Slice 1 cannot exceed 70% capacity. |
| **`dedicated_full`** | Slice 1 active, Slice 2 active | S1: Ded=30%, S2: Ded=30% | Both slices receive their guaranteed dedicated allocations (30% each) + equal shared capacity (50% each). |
| **`min_idle`** | Slice 1 active, Slice 2 idle | S1: Min=40%, S2: Min=40% | Slice 2 prioritized PRBs (40%) are shareable when idle, allowing Slice 1 to burst up to 100% capacity. |
| **`min_full`** | Slice 1 active, Slice 2 active | S1: Min=40%, S2: Min=40% | Both slices receive at least 40% guaranteed minimum under contention (50% each). |
| **`max_idle`** | Slice 1 active, Slice 2 idle | S1: Max=50%, S2: Max=50% | Slice 1 is strictly hard-capped at 50% despite 50% idle / unused bandwidth on the channel. |
| **`max_full`** | Slice 1 active, Slice 2 active | S1: Max=30%, S2: Max=70% | Bandwidth divides proportionally 30%/70% respecting hard upper limits. |

---

## 1. Live 5G rfsim Testbed Execution (with Tmux Visuals)

Make sure the 5G network is running:
```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts
./bringup.py --build-quick
```

Then launch any test scenario in **interactive tiled tmux panes** from `nws/test`:

```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/test

# Launch interactive menu (opens single unified tmux session by default):
python3 test.py

# Or launch specific test cases directly from testcases/:
./testcases/tc_101_as_no_slice_dl_udp.sh
./testcases/tc_201_dedicated_sym_idle_dl_udp.sh
./testcases/tc_205_dedicated_sym_full_dl_udp.sh
./testcases/tc_301_min_sym_idle_dl_udp.sh
./testcases/tc_305_min_sym_full_dl_udp.sh
./testcases/tc_401_max_sym_idle_dl_udp.sh
./testcases/tc_405_max_sym_full_dl_udp.sh
```

### Tmux 2-Tab Layout & Mouse Navigation
When launched, tmux opens **2 dedicated interactive tabs** with mouse cursor and clicking enabled by default:
- **Tab 0 (`servers`):** 5 tiled interactive panes displaying UPF Core server reception (`/tmp/iperf_server_1.log` to `5.log`) with live receiver bitrates, loss percentages, and jitter.
- **Tab 1 (`clients`):** 5 tiled interactive panes displaying all UE clients (`nws-oai-nr-ue1` to `nws-oai-nr-ue5`) running continuous traffic generation (`iperf3` or `ping`) with live transfer statistics.

**Mouse & Cursor Navigation:**
- Click on `0:servers` or `1:clients` in the status bar at the bottom to switch tabs.
- Click on any pane to focus and scroll history with your mouse wheel.
- Drag pane borders with the mouse to resize.

---

---

## 2. Dedicated Timestamped Logging

All test runs automatically record full execution logs under `nws/test/logs/<test_name>_<YYYYMMDD_HHMMSS>/`:
- `test_summary.log`: Test metadata, active & idle UEs, test duration, and slice configurations.
- `mac_stats.log`: Timestamped gNB MAC & slice PRB allocations polled every second.
- `ue1_traffic.log`: Live `iperf3` client bitrate stream from UE1.
- `ue2_traffic.log`: Live `iperf3` client bitrate stream or `[IDLE]` verification from UE2.
- `xapp_policy_applied.json`: Applied slice configuration payload.
- `logs/latest`: Symlink pointing to the most recent test run.

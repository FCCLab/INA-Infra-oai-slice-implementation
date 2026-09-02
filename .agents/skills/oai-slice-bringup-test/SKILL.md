---
name: oai-slice-bringup-test
description: >-
  Bring up, configure, and test the 5G Network Slicing system (Open5GS 5GC, OAI gNB rfsim,
  FlexRIC, and UEs) using nws/scripts/bringup.py, test_ping.py, test_throughput.py, and FlexRIC xApp.
  Use this skill whenever the user asks to bring up the 5G network, test slicing, run ping/throughput
  benchmarks, start/stop the testbed, or debug rfsim end-to-end connectivity.
---

# OAI Network Slicing Bringup & Testing Guide

This skill provides step-by-step procedures and runbooks for deploying, testing, and controlling the 5G Network Slicing testbed using [bringup.py](file:///home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts/bringup.py).

---

## 1. Quick Reference Commands

All commands are executed from `nws/scripts/`:

```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts

# Standard bringup: 5 UEs, NS BOTH (DL+UL Slicing), 133 PRB, quick build
./bringup.py --build-quick

# UL-only Slicing with 5 UEs (Stable)
./bringup.py --sch NSUL --no-build

# CU/DU/CU-UP Split Architecture (133 PRB)
./bringup.py --sch NSBOTH --split --build-quick

# Multi-UPF + Multi-CU-UP (1 per slice, 133 PRB)
./bringup.py --split-mult-upf-cuup --sch NSBOTH --build-quick

# Stop RAN + RIC
./bringup.py down

# Stop everything (RAN + RIC + Open5GS 5GC)
./bringup.py down --with-core
```

---

## 2. Core Bringup Options (`bringup.py`)

| Parameter | Options / Syntax | Default | Description |
| :--- | :--- | :--- | :--- |
| **`--sch`** | `NSBOTH` (or `BOTH`), `NSUL` (or `NS`), `NSDL`, `PF` | `NSBOTH` | MAC scheduler type for DL and UL. |
| **`--ues`** | `1` .. `5` | `5` | Number of UEs to attach (each assigned to a dedicated S-NSSAI slice). |
| **`--bw`** | `133`, `106` | `133` | Channel bandwidth in PRBs (133 PRB default). |
| **`--split`** | Flag | `False` | Deploy CU-CP (`nr-softmodem`), CU-UP (`nr-cuup`), and DU (`nr-softmodem`) over F1/E1. |
| **`--split-mult-upf-cuup`** | Flag | `False` | Deploy 5 separate CU-UPs and 5 UPFs (one dedicated per slice). |
| **`--build-quick`** | Flag | `False` | Fast packaging using local `cmake` build into `oai-gnb:latest`. |
| **`--no-build`** | Flag | `False` | Start containers immediately using existing Docker images. |
| **`--force-rebuild-oai`**| Flag | `False` | Force clean `--no-cache` Docker image rebuild of `ran-build` and `oai-gnb`. |
| **`--no-ric`** | Flag | `False` | Skip starting the nearRT-RIC container. |
| **`--no-ping`** | Flag | `False` | Skip automated post-bringup L3 ping verification. |

### Scheduler Modes (`--sch`)
- **`NSBOTH`**: Slicing enabled in both DL (`dl_rb_alloc = nr_dl_slice_proportional_fair`) and UL (`ul_rb_alloc = nr_ul_slice_proportional_fair`).
- **`NSUL`**: Slicing in UL, standard proportional fair in DL.
- **`NSDL`**: Slicing in DL, standard proportional fair in UL.
- **`PF`**: Baseline standard proportional fair (no slicing).

---

## 3. End-to-End Testing Workflow

### Step 1: Bring up the Network
```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts
./bringup.py --build-quick
```
*Verification:* The script waits for gNB sync, attaches UEs, checks PDU session IP allocation (`10.45.0.31`–`10.45.0.35`), and runs an initial ping to `10.45.0.1`.

### Step 2: Test L3 Connectivity (`test_ping.py`)
Ping the UPF (`10.45.0.1`) from all active UE containers:
```bash
# One-shot sequential ping verification across all UEs
./test_ping.py

# Multi-pane continuous ping monitoring in tmux
./test_ping.py --tmux
```

### Step 3: Test Throughput & Slicing Enforcement (`test_throughput.py`)
Run iperf3 benchmarks between UEs and the core (`nws-5gc`):
```bash
# Uplink throughput test across all UEs (sequential)
./test_throughput.py --dir ul

# Downlink throughput test (parallel across all UEs)
./test_throughput.py --dir dl --mode parallel

# Bidirectional throughput test (20 seconds duration)
./test_throughput.py --dir both --time 20

# Test a single UE (e.g. UE1)
./test_throughput.py --ue1 --dir ul

# UDP traffic test with bandwidth targets
./test_throughput.py -u --dir ul
```

### Step 4: 3GPP Network Slicing Matrix Tests in Tmux (`nws/test`)
Run live interactive tests across the 6 slicing dimensions (dedicated/min/max idle & full) with live tiled monitoring panes and dedicated timestamped logging:
```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/test

# Interactive menu:
./menu.sh

# Or direct test case scripts:
./testcases/tc_101_no_slice_dl_udp.sh
./testcases/tc_201_dedicated_sym_idle_dl_udp.sh
./testcases/tc_205_dedicated_sym_full_dl_udp.sh
./testcases/tc_301_min_sym_idle_dl_udp.sh
./testcases/tc_305_min_sym_full_dl_udp.sh
./testcases/tc_401_max_sym_idle_dl_udp.sh
./testcases/tc_405_max_sym_full_dl_udp.sh
```
*Logs:* Automatically saved per-test with timestamps in `nws/test/logs/<test_name>_<timestamp>/`.

### Step 5: FlexRIC xApp Runtime Monitoring & Control
The FlexRIC xApp provides real-time slice statistics and dynamic E2 control via REST API:
```bash
# Start the xApp
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts/xapp
docker compose up -d --build

# Query active slice configurations and statistics
curl -s http://127.0.0.1:18080/api/v1/slices | jq .

# Swagger API docs available at: http://127.0.0.1:18080/docs
```

---

## 5. Teardown & Cleanup

```bash
cd /home/tuannv/INA-Infra-oai-slice-implementation/nws/scripts

# Stop RAN and RIC containers
./bringup.py down

# Full teardown including Open5GS core network
./bringup.py down --with-core
```

---

## 6. Troubleshooting & Diagnostics

- **Check gNB logs:**
  ```bash
  docker logs -f nws-oai-gnb
  ```
  Look for: `MAC scheduler initialized: DL=SCHE_NS (rb_alloc=nr_dl_slice_proportional_fair)` and `[NR_MAC] Frame ... Slot ... Slice SST 0x01 SD 0x000001: --- Required PRBs ...`.

- **Check UE container status & PDU session interface:**
  ```bash
  docker exec nws-oai-nr-ue1 ip addr show oaitun_ue1
  ```

- **Inspect real-time Telnet MAC & Slice stats:**
  ```bash
  telnet 127.0.0.1 9090
  # Inside telnet:
  mac stats
  ```

- **Inspect Core (Open5GS) logs:**
  ```bash
  docker logs -f nws-5gc
  ```

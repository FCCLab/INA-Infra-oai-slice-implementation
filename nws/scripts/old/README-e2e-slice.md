# E2E network slicing (Docker)

## Prerequisites

1. **5GC**: From repo root, `nws/5gc/open5gs` creates Docker network `nws-n2n3` and container `nws-5gc`.
2. **RAN**: `nws/docker-compose/docker-compose.open5gs.5slices.nsul.yaml` expects **external** network `nws-n2n3` (start 5GC first). Build OAI images from `nws/docker-compose/` if needed: `docker compose -f docker-compose.open5gs.5slices.nsul.yaml build`.
3. **Tools**: `docker`, `docker compose`, `iperf3` inside `nws-5gc` and UE images, `mongosh` in `nws-5gc`.

## Run

```bash
cd /path/to/repo/nws
# Quick bringup (default: 5 UEs, NS UL scheduler) + PDU ping check
python3 scripts/bringup.py
python3 scripts/bringup.py --ues 3 --sch NS
python3 scripts/bringup.py --ues 2 --sch PF

# Full e2e (iperf / relative share checks)
python3 scripts/e2e_nw_slice_docker.py
```

Options (see script `--help`):

- `--iperf-host` / `--iperf-port`: iperf server reachable from UE (default `10.47.0.2:5201` — core N6 IP; entrypoint starts iperf on 5201).
- `--skip-start`: only verify subscribers + run tests (stack already up).
- `--strict-relative`: optional check that the UE with highest `min_prb_ratio` (slice SD `0x000002` in default YAML) gets at least `--relative-floor` × median of the other UEs in parallel UL.
- `--with-flexric`: also start `nws-nearRT-RIC` after gNB/UEs.
- `--no-ran-compose-down-first`: skip `docker compose down --remove-orphans` before RAN `up` (default is **to** run it for a clean network state).

The script runs **`docker compose down --remove-orphans`** on the RAN file by default, then starts **gNB first**, then **all five UE containers**. All **`docker compose up`** calls in this script use **`--remove-orphans`** (including when starting the core).

Logs: `nws/logs/e2e_slice_<timestamp>.log`

## Throughput vs slice YAML

gNB `Slices:` PRB percentages are **not** equal to iperf Mbps. The script uses sanity thresholds and optional relative ordering, not strict PRB equality.

# nws scripts

Lab helpers for Open5GS + OAI rfsim + FlexRIC network slicing.

| Script | Purpose |
|--------|---------|
| `bringup.py` | Start 5GC + nearRT-RIC + gNB + N UEs, verify PDU ping |
| `test_ping.py` | Ping UPF from running UE containers |
| `test_throughput.py` | iperf3 UL/DL from running UEs |
| `xapp/` | FlexRIC xApp: monitor NS policy + REST/Swagger control |

Older utilities live under `old/` and `test_scripts/`.

## Quick start

```bash
cd nws/scripts
./bringup.py                  # 5 UEs, NS UL (stable default)
./bringup.py --sch NSBOTH     # experimental DL+UL NS (see known issue below)
./test_ping.py
./test_throughput.py --dir both

cd xapp && docker compose up --build
# Swagger: http://127.0.0.1:18080/docs
```

## `bringup.py`

```bash
./bringup.py                         # defaults below
./bringup.py --ues 3 --sch NSUL
./bringup.py --ues 5 --sch NSDL
./bringup.py --ues 5 --sch NSBOTH    # experimental
./bringup.py --ues 2 --sch PF
./bringup.py --no-build              # skip image rebuild
./bringup.py --no-ric                # skip FlexRIC
./bringup.py --no-ping
```

| Flag | Default | Notes |
|------|---------|--------|
| `--ues` | `5` | 1..5 |
| `--sch` | `NSUL` | `NS`/`NSUL`, `NSDL`, `NSBOTH`/`BOTH`, `PF` |
| `--no-build` | off | Skip OAI recompile + compose `--build` |
| `--force-rebuild-oai` | off | Always recompile `ran-build` + `oai-gnb` |
| `--ping-host` | `10.45.0.1` | UPF via oaitun |

`--sch` mapping:

| Value | DL | UL | Status |
|-------|----|----|--------|
| `NS` / `NSUL` (default) | PF | NS | **stable** (lab default) |
| `NSDL` | NS | PF | needs gNB rebuild with DL `remainUEs` fix |
| `NSBOTH` / `BOTH` | NS | NS | experimental; same DL fix as NSDL |
| `PF` | PF | PF | stable |

Compose/YAML base for 5 UEs is `docker-compose.open5gs.5slices.nsul.yaml` +
`configs/gnb/gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsul.yaml`.
For `NSBOTH` / `PF` (non-dedicated), bringup **runtime-patches**
`dl_scheduler_type`/`ul_scheduler_type` into a temp YAML.

### DL NS fix (`NSDL` / `NSBOTH`)

Older images reset the DL DCI/UE budget inside every per-slice `pf_dl()` call
(UL already shared `remainUEs` across slices). With 5 slices that over-schedules
PDCCH, starves ping/SRB DL, and can trigger RLC max RETX / reestablishment /
`get_searchspace()` abort.

Fix in tree (`gNB_scheduler_dlsch.c`): share `remainUEs` across DL slices and
remap DL HARQ retx inside the current slice window.

**Build note:** `docker compose --build` only *packages* `ran-build:latest`.
Default `./bringup.py` (without `--no-build`) recompiles when OAI MAC sources
are newer than `ran-build:latest` via `build_ran_build.sh` + `build_oai_gnb.sh`:

```bash
./bringup.py --sch NSDL                 # rebuild OAI if sources changed, then bring up
./bringup.py --sch NSDL --force-rebuild-oai
./bringup.py --sch NSUL --no-build       # use existing images only
```

Prefer `--sch NSUL` if you cannot wait for a rebuild yet.

## Default slice configuration

Source YAML:

`configs/gnb/gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsul.yaml`

Checked-in nsul file is UL-only NS (`dl=0`, `ul=1`). With `--sch NSDL` /
`NSBOTH`, DL NS uses the same dedicated/min/max ratios (no `dl_*` / `ul_*`
overrides in the default YAML).

### DL (NS under `NSBOTH`)

| slice_id | SST | SD | dedicated% | min% | max% | Notes |
|----------|-----|-----|------------|------|------|--------|
| 0 | 1 | `0xffffff` | 0 | 0 | 100 | default / unused |
| 1 | 1 | `0x000001` | 0 | 0 | 100 | UE1 |
| 2 | 1 | `0x000002` | 0 | 0 | 100 | UE2 |
| 3 | 1 | `0x000003` | 0 | 0 | 50 | UE3 |
| 4 | 1 | `0x000004` | 0 | 0 | 50 | UE4 |
| 5 | 1 | `0x000005` | 0 | 0 | 50 | UE5 |

### UL (NS under `NSBOTH` / `NSUL`)

| slice_id | SST | SD | dedicated% | min% | max% | Notes |
|----------|-----|-----|------------|------|------|--------|
| 0 | 1 | `0xffffff` | 0 | 0 | 100 | default / unused |
| 1 | 1 | `0x000001` | 0 | 0 | 100 | UE1 |
| 2 | 1 | `0x000002` | 0 | 0 | 100 | UE2 |
| 3 | 1 | `0x000003` | 0 | 0 | 50 | UE3 |
| 4 | 1 | `0x000004` | 0 | 0 | 50 | UE4 |
| 5 | 1 | `0x000005` | 0 | 0 | 50 | UE5 |

Rules: `dedicated ≤ min ≤ max`, each in `[0, 100]`; sum of `dedicated` ≤ 100%
**per direction**. Optional YAML overrides: `dl_dedicated_prb_ratio` /
`dl_min_prb_ratio` / `dl_max_prb_ratio` and `ul_*` equivalents.

UE → slice (NSSAI in `configs/ue/nrueN.uicc.yaml`): UE *N* uses SST `1` / SD
`0x00000N` (N=1..5). PDU IPv4: `10.45.0.3N`.

After bringup, the printed `=== slice config ===` table reflects the effective
(patched) gNB YAML. The xApp reports the same rows twice (`direction: dl` and
`direction: ul`) when both schedulers are NS.

## Runtime NS policy (xApp)

See [`xapp/readme.md`](xapp/readme.md).

- Monitor E2 `ns_policy` (actual NS PRB policy, not FlexRIC STATIC/NVS demo)
- REST + Swagger on host port **18080**
- Confirm SET with GET `/api/v1/slices` or gNB log `NS E2 SET applied`

```bash
curl -s http://127.0.0.1:18080/api/v1/slices | jq .
```

## Tests

```bash
# L3 ping UPF from all running UEs
./test_ping.py
./test_ping.py --tmux          # forever panes

# iperf3 (server in nws-5gc)
./test_throughput.py --dir ul
./test_throughput.py --dir dl --mode parallel
./test_throughput.py --dir both --time 20
./test_throughput.py --ue1 --dir ul                 # single UE
./test_throughput.py --ue1 --ue3 --tmux --dir ul    # subset
./test_throughput.py --tmux --dir ul --interval 5   # smoother forever panes (default -i 5)
# Leaving the test (detach/Ctrl-c/kill-session) clears UE+core iperf3
# For stable per-UE Mbps: sequential (default) or one UE; parallel+NSBOTH looks bursty at -i 1
```

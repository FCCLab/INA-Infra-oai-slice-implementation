# NS slice E2 control (Python xApp)

Monitor and update OAI **network slicing** PRB ratios (`dedicated` / `min` / `max`, per `ul`/`dl`) over E2 Slice SM (ID 145).

## Prerequisites

- gNB built with E2 (`--build-e2`) and NS UL/DL scheduler (`ul_scheduler_type` / `dl_scheduler_type` = 1 in gNB YAML).
- nearRT-RIC running (FlexRIC).
- Python xApp SDK: run `nws/build_flexric.sh` (enables `XAPP_MULTILANGUAGE` and SWIG 4.1+).

### Docker `nws-nearRT-RIC`

The compose file bind-mounts `openairinterface5g/`, which hides the FlexRIC build baked into the image. Before starting the stack:

```bash
cd nws && ./build_flexric.sh
```

`scripts/run_flexric.sh` uses `openair2/E2AP/flexric/build/examples/ric/nearRT-RIC` and stages `lib*_sm.so` from that build. If the host tree has no build, the script falls back to `/usr/local/bin/nearRT-RIC` and `/usr/local/lib/flexric` from the image.

## Stack (example: 3 slices NS UL)

```bash
cd nws/docker-compose
docker compose -f docker-compose.open5gs.3slices.nsul.yaml up -d
# start gNB with matching 3-slice nsul YAML and E2 enabled
```

## Run xApps

From the FlexRIC build tree (where `xapp_sdk` is on `PYTHONPATH`):

```bash
cd nws
source configs/flexric/flexric.connection.env   # NEAR_RIC_IP, FLEXRIC_CONF, PYTHONPATH
PY=../openairinterface5g/openair2/E2AP/flexric/build/examples/xApp/python3

# Terminal 1 — read tenant slice ratios every second
python3 ${PY}/xapp_ns_slice_monitor.py

# Terminal 2 — push new UL/DL min/max policy (must satisfy dedicated <= min <= max)
export NS_SLICE_SET_JSON='[{"sst":1,"sd":"0x000002","direction":"ul","dedicated":10,"min":10,"max":100}]'
python3 ${PY}/xapp_ns_slice_set.py

# Optional: SET then print indications every second for 30s
python3 ${PY}/xapp_ns_slice_set.py --set-json policy.json --verify --duration 30
```

Indications print JSON like:

```json
{
  "tstamp": 1234567890,
  "slices": [
    {"sst": 1, "sd": "0x000001", "direction": "ul", "dedicated": 5.0, "min": 10.0, "max": 40.0},
    {"sst": 1, "sd": "0xffffff", "direction": "ul", "dedicated": 0.0, "min": 0.0, "max": 100.0}
  ]
}
```

All configured slices are reported (including default `0xffffff`). SET still rejects `sd=0xffffff`.

Empty `"slices": []` means NS scheduler is off on that direction or the gNB E2 agent is not reporting NS policy.

NS scheduler rule: **dedicated ≤ min ≤ max** (percent), enforced on the **gNB E2 agent** (`ran_func_slice.c`). The xApp forwards SET as-is. E2 **CONTROL ACK** only means the RIC got a reply — confirm on the monitor or in gNB log (`NS E2 SET applied` vs MAC error diagnostic).

## SET policy (details)

```bash
python3 xapp_ns_slice_set.py --set-json policy.json
```

Or:

```bash
python3 xapp_ns_slice_policy.py --set-json policy.json
```

Rules match gNB config: `dedicated <= min <= max`, each ratio in `[0, 100]`, sum of dedicated ≤ 100% per direction.

Debug snapshot: `rt_ns_slice_policy.json` in the working directory.

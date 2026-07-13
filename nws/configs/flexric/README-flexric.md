# FlexRIC connection config

| File | Purpose |
|------|---------|
| [`flexric.conf`](flexric.conf) | nearRT-RIC / xApp INI (`NEAR_RIC_IP`, `DB_DIR`, `DB_NAME`) |
| [`flexric.connection.env`](flexric.connection.env) | Host shell: `NEAR_RIC_IP`, `FLEXRIC_CONF`, `PYTHONPATH` |

## Docker stack

Compose mounts `configs/flexric/flexric.conf` as `/workspace/flexric.conf` in `nws-nearRT-RIC`.

Ensure gNB YAML matches:

```yaml
e2_agent:
  near_ric_ip_addr: 192.168.201.142   # same as NEAR_RIC_IP
  # Must match the FlexRIC build used for OAI (not the image default).
  sm_dir: /workspace/openairinterface5g/openair2/E2AP/flexric/build/flexric_plugins/
```

Using `sm_dir: /usr/local/lib/flexric/` with a host-built gNB loads stale plugins and can segfault during E2 setup.

## OAI gNB (E2 agent)

Rebuild after OAI/FlexRIC changes. Use `nws/build_oai.sh` (`E2AP_V3`, `KPM_V3_00`). If cmake was previously configured with V2, clear the cache first:

```bash
cd nws
CLEAN=1 ./build_oai.sh
```

## Host Python xApp

```bash
cd nws
./build_flexric.sh
source configs/flexric/flexric.connection.env
python3 ../openairinterface5g/openair2/E2AP/flexric/build/examples/xApp/python3/xapp_ns_slice_monitor.py
# SET (other terminal): xapp_ns_slice_set.py
```

Or point at a custom config:

```bash
export FLEXRIC_CONF=/path/to/flexric.conf
```

## Troubleshooting: no “gNB connected” in RIC logs

FlexRIC does **not** print `gNB connected`. A successful attach looks like:

**nearRT-RIC (`docker logs nws-nearRT-RIC`):**

```text
[NEAR-RIC]: nearRT-RIC IP Address = 192.168.201.142, PORT = 36421
[NEAR-RIC]: Loading SM ID = 145 with def = ...
[E2AP]: E2 SETUP-REQUEST rx from PLMN 1.01 Node ID 3584 RAN type ngran_gNB
```

**gNB (`nws/log/gnb.log`):**

```text
[E2-AGENT]: E2 SETUP-REQUEST tx
[E2-AGENT]: E2 SETUP RESPONSE rx
```

If the RIC was restarted after the gNB, the gNB will not reconnect until it resends setup (or you restart it):

```bash
docker restart nws-oai-gnb
```

Checklist:

- gNB built with `--build-e2` (`nws/build_oai.sh`)
- `e2_agent.near_ric_ip_addr` in gNB YAML = `NEAR_RIC_IP` in `configs/flexric/flexric.conf` (default `192.168.201.142`)
- Both containers on `nws-oai-rf-sim` network
- `docker logs nws-nearRT-RIC 2>&1 | tail -50` (not only the three `Starting nearRT-RIC` lines from the wrapper script)

## Change RIC IP

1. Edit `NEAR_RIC_IP` in `configs/flexric/flexric.conf`
2. Update `nws-nearRT-RIC` `ipv4_address` in your compose file
3. Update `near_ric_ip_addr` in the gNB YAML

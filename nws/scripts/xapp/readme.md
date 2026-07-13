# FlexRIC Python xApps (nws)

Monitor OAI **NS PRB slice policy** over E2 Slice SM (RAN func 145), using the
same `oai-flexric:latest` image as `nws-nearRT-RIC`.

## Build base image (once)

```bash
nws/build_scripts/build_oai_flexric.sh
```

That builds `openairinterface5g/openair2/E2AP/flexric/docker/Dockerfile.flexric.ubuntu`.

## Run slice monitor

Start nearRT-RIC via bringup / RAN compose first (`nws-nearRT-RIC` at
`192.168.201.142`). Then:

```bash
cd nws/scripts/xapp
docker compose up --build
```

This starts `nws-xapp-slice-monitor` at `192.168.201.143`.

Requires gNB already on `nws-oai-rf-sim` with:

```yaml
e2_agent:
  near_ric_ip_addr: 192.168.201.142
  sm_dir: /usr/local/lib/flexric/
```

After RIC starts, **restart the gNB** if it was already up so E2 setup can complete:

```bash
docker restart nws-oai-gnb
```

Wait until `docker logs nws-nearRT-RIC` shows `E2 SETUP-REQUEST rx` / Accepting RAN function … 145, then start the xApp. If the xApp hangs after `DB filename = /tmp/xapp_db`, the RIC is down or unreachable — check `docker ps -a --filter name=nws-nearRT-RIC`.

## What you get

Indications carry two slice models; this xApp focuses on the real one:

| Field | Meaning |
|-------|---------|
| `ns_policy` / `slices` | **OAI NS** PRB ratios: SST/SD, `ul`/`dl`, dedicated/min/max % |
| `flexric` | FlexRIC STATIC/NVS/EDF demo (ignore for NS lab work) |

Outputs:

- `out/rt_ns_slice_policy.json` — NS policy only (what you usually want)
- `out/rt_slice_stats.json` — full indication including FlexRIC demo

`--print` reprints NS JSON when the policy changes (not every 10 ms). Use
`--print-flexric` only if you need the demo STATIC/NVS dump.

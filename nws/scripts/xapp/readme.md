# FlexRIC Python xApp (nws) — NS monitor + REST control

Monitor OAI **NS PRB slice policy** over E2 Slice SM (RAN func 145) and change
it via a REST API (`control_ns_slice_policy`).

## Run

```bash
cd nws/scripts/xapp
docker compose up --build
```

Requires `nws-nearRT-RIC` at `192.168.201.142` and gNB E2 attached. If the xApp
hangs after `DB filename`, check that the RIC is running.

## REST API (host network, port 18080)

Slice GUI (live monitor + edit): [http://127.0.0.1:18080/gui](http://127.0.0.1:18080/gui)  
Swagger UI on this host: [http://10.1.132.13:18080/docs](http://10.1.132.13:18080/docs)  
(also [http://127.0.0.1:18080/docs](http://127.0.0.1:18080/docs))  
OpenAPI JSON: [http://10.1.132.13:18080/openapi.json](http://10.1.132.13:18080/openapi.json)

Override the advertised lab URL with `NWS_XAPP_LAB_IP` (e.g. `10.1.132.13`).

```bash
# Current policy (from last indication)
curl -s http://127.0.0.1:18080/api/v1/slices | jq .

# SET one or more slices (dedicated <= min <= max; no sd=0xffffff)
curl -s -X PUT http://127.0.0.1:18080/api/v1/slices \
  -H 'Content-Type: application/json' \
  -d '{"slices":[
    {"sst":1,"sd":"0x000002","direction":"ul","dedicated":10,"min":10,"max":100}
  ]}' | jq .

# PATCH merge one entry into current policy (excluding 0xffffff), then SET
curl -s -X PATCH http://127.0.0.1:18080/api/v1/slices \
  -H 'Content-Type: application/json' \
  -d '{"sst":1,"sd":"0x000003","direction":"ul","dedicated":0,"min":0,"max":40}' | jq .
```

Also on the host: `http://127.0.0.1:18080/...` (compose uses `network_mode: host`).

E2 CONTROL ACK only means the RIC got a reply — confirm with another GET or
gNB log line `NS E2 SET applied`.

## Outputs

| File | Content |
|------|---------|
| `out/rt_ns_slice_policy.json` | NS policy only |
| `out/rt_slice_stats.json` | full indication (`ns_policy` + FlexRIC demo) |

`--print` reprints NS JSON when the policy changes.

# nws xApp — NS monitor + E2 control

Near-RT FlexRIC xApp for OAI **NS PRB slice policy** (E2 Slice SM, RAN func 145).

Same Docker image, two ports:

| Process | Port | What |
|---|---|---|
| **Backend** | `18080` | Actual function (E2 subscribe + `control_ns_slice_policy`) and Swagger REST |
| **Frontend** | `18081` | Operator console (live vs SET dedicated/min/max) |

## Run

```bash
cd nws/app_xapp
docker compose up --build
```

Requires `nws-nearRT-RIC` at `192.168.201.142` and gNB E2 attached.

- Console: http://127.0.0.1:18081/
- Swagger: http://127.0.0.1:18080/docs
- Health: `curl -s http://127.0.0.1:18080/health`

```bash
curl -s http://127.0.0.1:18080/api/v1/slices | jq .
curl -s -X PUT http://127.0.0.1:18080/api/v1/slices \
  -H 'Content-Type: application/json' \
  -d '{"slices":[{"sst":1,"sd":"0x000002","direction":"ul","dedicated":10,"min":10,"max":100}]}'
```

Rules: `dedicated ≤ min ≤ max`, each in `[0, 100]`, sums per direction ≤ 100%, `sd=0xffffff` cannot be SET.

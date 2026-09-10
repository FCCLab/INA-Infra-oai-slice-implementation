# nws rApp — O-RAN A1 Slice SLA

Non-RT rApp for policy type **ORAN_SliceSLATarget_3.0.0** (ETSI TS 103 988 /
O-RAN.WG2.A1TD). Operator input is A1 JSON (`scope` + `sliceSlaObjectives` +
optional `sliceSlaResources`). **Send to xApp is the A1 object as-is — not PRB %.**

| Process | Port | What |
|---|---|---|
| **Backend** | `18090` | Validate / store A1 policies, PUT them to xApp, Swagger |
| **Frontend** | `18091` | Console to compose SLA and send to xApp |

xApp southbound: `PUT http://127.0.0.1:18080/api/v1/a1/slice-sla`

## Run

```bash
cd nws/app_xapp && docker compose up -d --build   # near-RT receiver
cd nws/app_rapp && docker compose up -d --build
```

- Console: http://127.0.0.1:18091/
- Swagger: http://127.0.0.1:18090/docs

#!/bin/sh
# Same image: frontend console + backend REST (different ports).
set -eu

API_PORT="${NWS_RAPP_API_PORT:-18090}"
UI_PORT="${NWS_RAPP_UI_PORT:-18091}"
UI_HOST="${NWS_RAPP_UI_HOST:-0.0.0.0}"
XAPP_API="${NWS_XAPP_API_BASE:-http://127.0.0.1:18080}"

python3 -u /rapp/frontend/serve.py \
  --host "$UI_HOST" \
  --port "$UI_PORT" \
  --backend-port "$API_PORT" &

if [ "$#" -gt 0 ]; then
  exec python3 -u /rapp/backend/rapp_slice.py "$@"
fi

A1_PMS="${NWS_A1_PMS_BASE:-http://policymanagementservice.nonrtric:8081/a1-policy-management/v1}"
A1_RIC="${NWS_A1_RIC_ID:-nearrt-ric}"
A1_TYPE="${NWS_A1_POLICY_TYPE_ID:-20008}"

exec python3 -u /rapp/backend/rapp_slice.py \
  --api-host 0.0.0.0 \
  --api-port "$API_PORT" \
  --xapp "$XAPP_API" \
  --a1-pms "$A1_PMS" \
  --a1-ric "$A1_RIC" \
  --a1-type "$A1_TYPE" \
  --store /rapp/out/rapp_store.json

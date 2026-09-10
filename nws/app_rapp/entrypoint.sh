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

exec python3 -u /rapp/backend/rapp_slice.py \
  --api-host 0.0.0.0 \
  --api-port "$API_PORT" \
  --xapp "$XAPP_API" \
  --store /rapp/out/rapp_store.json

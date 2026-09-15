#!/bin/sh
# Same image: frontend console + backend REST (different ports).
set -eu

API_PORT="${NWS_XAPP_API_PORT:-18080}"
UI_PORT="${NWS_XAPP_UI_PORT:-18081}"
UI_HOST="${NWS_XAPP_UI_HOST:-0.0.0.0}"

python3 -u /xapp/frontend/serve.py \
  --host "$UI_HOST" \
  --port "$UI_PORT" \
  --backend-port "$API_PORT" &

if [ "$#" -gt 0 ]; then
  exec python3 -u /xapp/backend/xapp_slice.py "$@"
fi

A1_MEDIATOR="${NWS_A1_MEDIATOR_URL:-http://service-ricplt-a1mediator-http.ricplt:10000}"
A1_TYPE="${NWS_A1_POLICY_TYPE_ID:-20008}"

exec python3 -u /xapp/backend/xapp_slice.py \
  --conf "${FLEXRIC_CONF:-/usr/local/etc/flexric/flexric.conf}" \
  --out /xapp/out/rt_slice_stats.json \
  --ns-out /xapp/out/rt_ns_slice_policy.json \
  --api-port "$API_PORT" \
  --a1-mediator "$A1_MEDIATOR" \
  --a1-type "$A1_TYPE" \
  --print

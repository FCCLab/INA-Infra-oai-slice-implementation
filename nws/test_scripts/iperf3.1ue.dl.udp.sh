#!/bin/bash
# 1 UE DL UDP: tmux — host :5201 server (top), UE1 client (bottom).
# -u UDP, -P 5, -b 100M per stream; -R = server→UE (download at UE). Session: tmux_iperf3.

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"

iperf3_require_tmux
iperf3_docker_route_to_host nws-oai-nr-ue1 "$HOST_IP"
iperf3_kill_session_if_exists "$SESSION"

tmux new-session -d -s "$SESSION" \; \
  split-window -v \; \
  select-pane -t "$SESSION:0.0" \; \
  send-keys 'echo "=== host iperf3 -s :5201 (DL UDP server) ==="' C-m \; \
  send-keys 'iperf3 -s -p 5201' C-m \; \
  select-pane -t "$SESSION:0.1" \; \
  send-keys "echo \"=== UE1 DL UDP -R -u -P 5 -b 100M -> ${HOST_IP} :5201 ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue1 iperf3 -c ${HOST_IP} -u -R -t 0 -p 5201 -P 5 -b 100M" C-m

iperf3_enable_mouse "$SESSION"
echo "Attaching to tmux session: $SESSION  (override: TMUX_IPERF_SESSION=…)"
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

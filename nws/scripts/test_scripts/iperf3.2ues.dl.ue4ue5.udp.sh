#!/bin/bash
# 2×2 tmux: top-left host :5201 server, top-right host :5202, bottom UE4/UE5 iperf3 UDP -R (DL) clients.
# -u UDP, -P 5 parallel streams, -b 100M per stream. -R reverses flow (server→UE).

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"

iperf3_require_tmux
iperf3_docker_route_to_host nws-oai-nr-ue4 "$HOST_IP"
iperf3_docker_route_to_host nws-oai-nr-ue5 "$HOST_IP"
iperf3_kill_session_if_exists "$SESSION"

# Grid: 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right
tmux new-session -d -s "$SESSION" \; \
  split-window -h \; \
  split-window -v \; \
  select-pane -t 0 \; \
  split-window -v \; \
  select-layout -t "$SESSION:0" tiled \; \
  select-pane -t "$SESSION:0.0" \; \
  send-keys 'echo "=== host iperf3 -s :5201 (DL UDP server) ==="' C-m \; \
  send-keys 'iperf3 -s -p 5201' C-m \; \
  select-pane -t "$SESSION:0.1" \; \
  send-keys 'echo "=== host iperf3 -s :5202 (DL UDP server) ==="' C-m \; \
  send-keys 'iperf3 -s -p 5202' C-m \; \
  select-pane -t "$SESSION:0.2" \; \
  send-keys "echo \"=== UE4 DL UDP -R -u -P 5 -b 100M -> ${HOST_IP} :5201 ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue4 iperf3 -c ${HOST_IP} -u -R -t 0 -p 5201 -P 5 -b 100M" C-m \; \
  select-pane -t "$SESSION:0.3" \; \
  send-keys "echo \"=== UE5 DL UDP -R -u -P 5 -b 100M -> ${HOST_IP} :5202 ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue5 iperf3 -c ${HOST_IP} -u -R -t 0 -p 5202 -P 5 -b 100M" C-m

iperf3_enable_mouse "$SESSION"
echo "Attaching to tmux session: $SESSION"
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

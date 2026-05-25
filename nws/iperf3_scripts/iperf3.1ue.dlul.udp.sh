#!/bin/bash
# 1 UE DL+UL UDP (concurrent): 2 host iperf3 servers — :5201 UL sink, :5202 DL source;
# UE1 runs two clients (UL bottom-left, DL -R bottom-right). tmux grid 2×2.
# -u UDP, -P 5, -b 100M per stream. Session: tmux_iperf3.

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"
PORT_UL="${IPERF3_PORT_UL:-5201}"
PORT_DL="${IPERF3_PORT_DL:-5202}"

iperf3_require_tmux
iperf3_docker_route_to_host nws-oai-nr-ue1 "$HOST_IP"
iperf3_kill_session_if_exists "$SESSION"

# Grid: 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right
tmux new-session -d -s "$SESSION" \; \
  split-window -h \; \
  split-window -v \; \
  select-pane -t 0 \; \
  split-window -v \; \
  select-layout -t "$SESSION:0" tiled \; \
  select-pane -t "$SESSION:0.0" \; \
  send-keys "echo \"=== host iperf3 -s :${PORT_UL} (UL UDP server) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${PORT_UL}" C-m \; \
  select-pane -t "$SESSION:0.1" \; \
  send-keys "echo \"=== host iperf3 -s :${PORT_DL} (DL UDP server) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${PORT_DL}" C-m \; \
  select-pane -t "$SESSION:0.2" \; \
  send-keys "echo \"=== UE1 UL UDP -> ${HOST_IP} :${PORT_UL} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue1 iperf3 -c ${HOST_IP} -u -t 0 -p ${PORT_UL} -P 5 -b 100M" C-m \; \
  select-pane -t "$SESSION:0.3" \; \
  send-keys "echo \"=== UE1 DL UDP -R -> ${HOST_IP} :${PORT_DL} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue1 iperf3 -c ${HOST_IP} -u -R -t 0 -p ${PORT_DL} -P 5 -b 100M" C-m

iperf3_enable_mouse "$SESSION"
echo "Attaching to tmux session: $SESSION  (override: TMUX_IPERF_SESSION=…)"
echo "Ports: UL server ${PORT_UL}, DL server ${PORT_DL} (IPERF3_PORT_UL / IPERF3_PORT_DL)"
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

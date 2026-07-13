#!/bin/bash
# Two tmux tabs: "servers" (4× host iperf3 -s) and "clients" (4× UE UDP clients).
# Flows: UE1 UL/DL + UE2 UL/DL — same ports as env defaults below.
# - UL: UE -> host (no -R). DL: host -> UE (-R).
# -u UDP, -P 5, -b 100M per stream (~500 Mbit/s per direction per UE).

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"
P_UL1="${IPERF3_PORT_UL_UE1:-5201}"
P_DL1="${IPERF3_PORT_DL_UE1:-5202}"
P_UL2="${IPERF3_PORT_UL_UE2:-5203}"
P_DL2="${IPERF3_PORT_DL_UE2:-5204}"

iperf3_require_tmux
iperf3_docker_route_to_host nws-oai-nr-ue1 "$HOST_IP"
iperf3_docker_route_to_host nws-oai-nr-ue2 "$HOST_IP"
iperf3_kill_session_if_exists "$SESSION"

# --- Window "servers": 2×2 tiled host listeners ---
tmux new-session -d -s "$SESSION" -n servers \; \
  split-window -h \; \
  split-window -v \; \
  select-pane -t "$SESSION:servers.0" \; \
  split-window -v \; \
  select-layout -t "$SESSION:servers" tiled \; \
  select-pane -t "$SESSION:servers.0" \; \
  send-keys "echo \"=== host iperf3 -s :${P_UL1} (UE1 UL sink) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${P_UL1}" C-m \; \
  select-pane -t "$SESSION:servers.1" \; \
  send-keys "echo \"=== host iperf3 -s :${P_DL1} (UE1 DL source) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${P_DL1}" C-m \; \
  select-pane -t "$SESSION:servers.2" \; \
  send-keys "echo \"=== host iperf3 -s :${P_UL2} (UE2 UL sink) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${P_UL2}" C-m \; \
  select-pane -t "$SESSION:servers.3" \; \
  send-keys "echo \"=== host iperf3 -s :${P_DL2} (UE2 DL source) ===\"" C-m \; \
  send-keys "iperf3 -s -p ${P_DL2}" C-m

# --- Window "clients": 2×2 tiled UE traffic ---
tmux new-window -t "$SESSION" -n clients \; \
  split-window -h \; \
  split-window -v \; \
  select-pane -t "$SESSION:clients.0" \; \
  split-window -v \; \
  select-layout -t "$SESSION:clients" tiled \; \
  select-pane -t "$SESSION:clients.0" \; \
  send-keys "echo \"=== UE1 UL UDP -> ${HOST_IP} :${P_UL1} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue1 iperf3 -c ${HOST_IP} -u -t 0 -p ${P_UL1} -P 5 -b 100M" C-m \; \
  select-pane -t "$SESSION:clients.1" \; \
  send-keys "echo \"=== UE2 UL UDP -> ${HOST_IP} :${P_UL2} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue2 iperf3 -c ${HOST_IP} -u -t 0 -p ${P_UL2} -P 5 -b 100M" C-m \; \
  select-pane -t "$SESSION:clients.2" \; \
  send-keys "echo \"=== UE1 DL UDP -R -> ${HOST_IP} :${P_DL1} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue1 iperf3 -c ${HOST_IP} -u -R -t 0 -p ${P_DL1} -P 5 -b 100M" C-m \; \
  select-pane -t "$SESSION:clients.3" \; \
  send-keys "echo \"=== UE2 DL UDP -R -> ${HOST_IP} :${P_DL2} ===\"" C-m \; \
  send-keys "docker exec -it nws-oai-nr-ue2 iperf3 -c ${HOST_IP} -u -R -t 0 -p ${P_DL2} -P 5 -b 100M" C-m

iperf3_enable_mouse "$SESSION"

echo "tmux windows: 0=servers  1=clients   (names: servers | clients)"
echo "Switch: Ctrl-b n / Ctrl-b p   or   Ctrl-b w pick window · mouse: click panes / drag borders / scroll"
echo "Ports: UL1=${P_UL1} DL1=${P_DL1} UL2=${P_UL2} DL2=${P_DL2}"
echo "Attaching on tab **clients** (servers already listening)."
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION:clients"

#!/bin/bash
# 5 UEs DL UDP: tmux window "srv" = host iperf3 -s :5201..5206, window "ue" = UE clients iperf3 -u -R.
# Layout stays split into 6 panes on each window; the 6th server runs but has no client attached.
# -u UDP, -P 5, -b 100M per stream. -R reverses flow (server→UE).
# Run from host shell (not inside containers).
# Switch windows: Ctrl-b then n (next) / p (previous).

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"
UDP_PARALLEL="${IPERF3_UDP_PARALLEL:-1}"
UDP_BW="${IPERF3_UDP_BW:-200M}"

iperf3_require_tmux

for ue in 1 2 3 4 5; do
  iperf3_docker_route_to_host "nws-oai-nr-ue${ue}" "$HOST_IP"
done

iperf3_kill_session_if_exists "$SESSION"

# --- srv: 6 panes total, start 6 servers; last one is spare ---
tmux new-session -d -s "$SESSION" -n srv
for _ in 1 2 3 4 5; do
  tmux split-window -h -t "$SESSION:srv.0"
done
tmux select-layout -t "$SESSION:srv" tiled

server_ports=(5201 5202 5203 5204 5205 5206)
client_ports=(5201 5202 5203 5204 5205)
for i in 0 1 2 3 4 5; do
  p="${server_ports[$i]}"
  tmux send-keys -t "$SESSION:srv.$i" "echo \"=== host iperf3 -s :${p} (DL UDP server) ===\"" C-m
  tmux send-keys -t "$SESSION:srv.$i" "iperf3 -s -p ${p}" C-m
done

# --- ue: 6 panes total, use 5 for clients and keep 1 empty ---
tmux new-window -t "$SESSION" -n ue
for _ in 1 2 3 4 5; do
  tmux split-window -h -t "$SESSION:ue.0"
done
tmux select-layout -t "$SESSION:ue" tiled

for i in 0 1 2 3 4; do
  ue=$((i + 1))
  p="${client_ports[$i]}"
  tmux send-keys -t "$SESSION:ue.$i" "echo \"=== UE${ue} DL UDP -R -u -P ${UDP_PARALLEL} -b ${UDP_BW} -> ${HOST_IP} :${p} ===\"" C-m
  tmux send-keys -t "$SESSION:ue.$i" "docker exec -it nws-oai-nr-ue${ue} iperf3 -c ${HOST_IP} -u -R -t 0 -p ${p} -P ${UDP_PARALLEL} -b ${UDP_BW}" C-m
done
tmux send-keys -t "$SESSION:ue.5" 'echo "=== unused pane (reserved for 6-way layout) ==="' C-m

tmux select-window -t "$SESSION:srv"
iperf3_enable_mouse "$SESSION"
echo "Windows: srv (6× iperf3 -s, last spare on :5206), ue (5× UE UDP clients + 1 unused)  |  tmux session: $SESSION"
echo "Attaching to tmux session: $SESSION"
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

#!/bin/bash
# 4 UEs UL UDP: tmux window "srv" = host iperf3 -s :5201..5204, window "ue" = UE clients iperf3 -u.
# -u UDP, -P 1, -b 20M per stream. Flow is UE -> host (no -R).
# Run from host shell (not inside containers).
# Switch windows: Ctrl-b then n (next) / p (previous).

set -uo pipefail

_IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=iperf3.common.sh
source "${_IPERF3_DIR}/iperf3.common.sh"

SESSION="${TMUX_IPERF_SESSION:-tmux_iperf3}"
HOST_IP="$(iperf3_resolve_host_ip)"
UDP_PARALLEL="${IPERF3_UDP_PARALLEL:-1}"
UDP_BW="${IPERF3_UDP_BW:-20M}"
NUM_UES=4

iperf3_require_tmux

for ue in $(seq 1 "$NUM_UES"); do
  iperf3_docker_route_to_host "nws-oai-nr-ue${ue}" "$HOST_IP"
done

iperf3_kill_session_if_exists "$SESSION"

# --- srv: 4 panes ---
tmux new-session -d -s "$SESSION" -n srv
for _ in 1 2 3; do
  tmux split-window -h -t "$SESSION:srv.0"
done
tmux select-layout -t "$SESSION:srv" tiled

ports=(5201 5202 5203 5204)
for i in 0 1 2 3; do
  p="${ports[$i]}"
  tmux send-keys -t "$SESSION:srv.$i" "echo \"=== host iperf3 -s :${p} (UL UDP sink) ===\"" C-m
  tmux send-keys -t "$SESSION:srv.$i" "iperf3 -s -p ${p}" C-m
done

# --- ue: 4 UL UDP clients ---
tmux new-window -t "$SESSION" -n ue
for _ in 1 2 3; do
  tmux split-window -h -t "$SESSION:ue.0"
done
tmux select-layout -t "$SESSION:ue" tiled

for i in 0 1 2 3; do
  ue=$((i + 1))
  p="${ports[$i]}"
  tmux send-keys -t "$SESSION:ue.$i" "echo \"=== UE${ue} UL UDP -u -P ${UDP_PARALLEL} -b ${UDP_BW} -> ${HOST_IP} :${p} ===\"" C-m
  tmux send-keys -t "$SESSION:ue.$i" "docker exec -it nws-oai-nr-ue${ue} iperf3 -c ${HOST_IP} -u -t 0 -p ${p} -P ${UDP_PARALLEL} -b ${UDP_BW}" C-m
done

tmux select-window -t "$SESSION:srv"
iperf3_enable_mouse "$SESSION"
echo "Windows: srv (4× iperf3 -s), ue (4× UE UL UDP clients)  |  tmux session: $SESSION"
echo "Attaching to tmux session: $SESSION"
iperf3_hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

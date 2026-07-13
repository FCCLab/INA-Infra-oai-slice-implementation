#!/bin/bash
# 5 UEs ping: one tmux window split into 6 panes; 5 panes run UE pings with auto-retry and the 6th stays unused.
# Run from the host shell (not inside containers). Switch panes: Ctrl-b then arrow keys.

set -uo pipefail

SESSION="${TMUX_PING_SESSION:-tmux_ping_5ues}"
PING_TARGET="${PING_TARGET:-10.45.0.1}"
PING_IFACE="${PING_IFACE:-oaitun_ue1}"
PING_RETRY_DELAY="${PING_RETRY_DELAY:-1}"

require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux not found; install tmux first." >&2
    exit 1
  fi
}

kill_session_if_exists() {
  local session="${1:?}"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Killing existing tmux session $session"
    tmux kill-session -t "$session"
  fi
}

enable_mouse() {
  local session="${1:?}"
  tmux set-option -t "$session" mouse on
}

hint_detach() {
  local session="${1:?}"
  echo "Detach without killing: Ctrl-b then d"
  echo "Kill session from another terminal: tmux kill-session -t $session"
}

require_tmux
kill_session_if_exists "$SESSION"

tmux new-session -d -s "$SESSION" -n ue
for _ in 1 2 3 4 5; do
  tmux split-window -h -t "$SESSION:ue.0"
done
tmux select-layout -t "$SESSION:ue" tiled

for i in 0 1 2 3 4; do
  ue=$((i + 1))
  tmux send-keys -t "$SESSION:ue.$i" "echo \"=== UE${ue} ping ${PING_TARGET} via ${PING_IFACE} ===\"" C-m
  tmux send-keys -t "$SESSION:ue.$i" "while true; do docker exec -it nws-oai-nr-ue${ue} ping ${PING_TARGET} -I ${PING_IFACE}; rc=\$?; echo \"=== UE${ue} ping exited with \${rc}; retrying in ${PING_RETRY_DELAY}s ===\"; sleep ${PING_RETRY_DELAY}; done" C-m
done
tmux send-keys -t "$SESSION:ue.5" 'echo "=== unused pane (reserved for 6-way layout) ==="' C-m

tmux select-window -t "$SESSION:ue"
enable_mouse "$SESSION"
echo "Window: ue (5× UE ping + 1 unused)  |  tmux session: $SESSION"
echo "Attaching to tmux session: $SESSION"
hint_detach "$SESSION"
exec tmux attach -t "$SESSION"

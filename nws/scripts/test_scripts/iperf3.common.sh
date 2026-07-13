#!/usr/bin/env bash
# Shared helpers for iperf3_*.sh tmux launchers. Source from the same directory:
#   _IPERF3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=iperf3.common.sh
#   source "${_IPERF3_DIR}/iperf3.common.sh"

iperf3_require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux not found; install tmux first." >&2
    exit 1
  fi
}

iperf3_resolve_host_ip() {
  echo "${IPERF3_HOST_IP:-${IPERF_HOST_IP:-10.47.0.1}}"
}

iperf3_kill_session_if_exists() {
  local session="${1:?}"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Killing existing tmux session $session"
    tmux kill-session -t "$session"
  fi
}

# Enable pane focus, border drag, and scroll with the mouse for this session.
iperf3_enable_mouse() {
  local session="${1:?}"
  tmux set-option -t "$session" mouse on
}

iperf3_docker_route_to_host() {
  local container="$1"
  local host_ip="${2:-$(iperf3_resolve_host_ip)}"
  local via_ip="${3:-${IPERF_VIA_IP:-10.45.0.1}}"
  local dev="${4:-${IPERF_OAI_DEV:-oaitun_ue1}}"
  echo "Routes ($container):"
  docker exec "$container" ip route || true
  echo "Adding ${host_ip}/32 via ${via_ip} dev ${dev} on $container"
  docker exec "$container" ip route add "${host_ip}/32" via "${via_ip}" dev "${dev}" || true
}

iperf3_hint_detach() {
  local session="${1:?}"
  echo "Detach without killing: Ctrl-b then d"
  echo "Kill session from another terminal: tmux kill-session -t $session"
}

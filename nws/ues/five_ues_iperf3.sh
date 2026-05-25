#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="nws-iperf3-servers"
N6_DEV="nws-n6"
PREFIX_LEN="24"
TMUX_WIDTH="240"
TMUX_HEIGHT="72"

# Requested IPs to add first.
ADD_IPS=(
  "10.47.0.101"
  "10.47.0.102"
  "10.47.0.103"
  "10.47.0.104"
  "10.47.0.105"
  "10.47.0.106"
)

# 6 iperf3 servers bound to 10.47.0.101 -> 10.47.0.106.
SERVER_IPS=(
  "10.47.0.101"
  "10.47.0.102"
  "10.47.0.103"
  "10.47.0.104"
  "10.47.0.105"
  "10.47.0.106"
)

SUDO=""
if [ "${EUID}" -ne 0 ]; then
  SUDO="sudo"
fi

if ! ip link show dev "${N6_DEV}" >/dev/null 2>&1; then
  echo "Interface '${N6_DEV}' not found."
  exit 1
fi

echo "Adding IP aliases on ${N6_DEV} ..."
for ip_addr in "${ADD_IPS[@]}"; do
  if ip -4 addr show dev "${N6_DEV}" | awk '{print $2}' | grep -qx "${ip_addr}/${PREFIX_LEN}"; then
    echo "  ${ip_addr}/${PREFIX_LEN} already exists"
  else
    echo "  adding ${ip_addr}/${PREFIX_LEN}"
    ${SUDO} ip addr add "${ip_addr}/${PREFIX_LEN}" dev "${N6_DEV}"
  fi
done

echo
echo "Current IPv4 addresses on ${N6_DEV}:"
ip -4 addr show dev "${N6_DEV}"

# Clean old session if any.
tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true

# Create a new tmux session with explicit size.
# Then split in a deterministic 2x3 layout (6 panes).
tmux new-session -d -x "${TMUX_WIDTH}" -y "${TMUX_HEIGHT}" -s "${SESSION_NAME}"
tmux split-window -h -t "${SESSION_NAME}:0"
tmux split-window -v -t "${SESSION_NAME}:0.0"
tmux split-window -v -t "${SESSION_NAME}:0.0"
tmux split-window -v -t "${SESSION_NAME}:0.1"
tmux split-window -v -t "${SESSION_NAME}:0.1"
tmux select-layout -t "${SESSION_NAME}:0" tiled

# Start one iperf3 server per pane, bound to each IP.
for idx in "${!SERVER_IPS[@]}"; do
  bind_ip="${SERVER_IPS[$idx]}"
  tmux send-keys -t "${SESSION_NAME}:0.${idx}" \
    "echo \"[pane ${idx}] iperf3 -s -B ${bind_ip} -p 5201\"; iperf3 -s -B ${bind_ip} -p 5201 -i 1" C-m
done

echo
echo "tmux session '${SESSION_NAME}' is ready."
echo "Attach with: tmux attach -t ${SESSION_NAME}"
tmux attach-session -t "${SESSION_NAME}"

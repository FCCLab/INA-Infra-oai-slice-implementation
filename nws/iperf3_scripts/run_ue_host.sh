#!/usr/bin/env bash
# Run OAI NR UE with RF simulator on the host (second terminal after gNB is up).
# Usage:
#   ./run_ue_host.sh [path/to/nr-ue.yaml]
# Example:
#   ./run_ue_host.sh ./nrue1.uicc.yaml
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/openairinterface5g/cmake_targets/ran_build/build"
CONFIG="${1:-${SCRIPT_DIR}/nrue1.uicc.yaml}"

if [[ ! -x "${BUILD_DIR}/nr-uesoftmodem" ]]; then
  echo "Build nr-uesoftmodem first (see run_gnb_host.sh)."
  exit 1
fi
if [[ ! -f "${CONFIG}" ]]; then
  echo "Config not found: ${CONFIG}"
  exit 1
fi

export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"
export OAI_GDBSTACKS=1
cd "${BUILD_DIR}"

echo "Starting UE with: ${CONFIG}"
exec ./nr-uesoftmodem -O "${CONFIG}" --rfsim -E \
  -r 106 --numerology 1 -C 3319680000 \
  --log_config.global_log_options level,nocolor,time "$@"

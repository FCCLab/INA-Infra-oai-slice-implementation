#!/usr/bin/env bash
# Run OAI gNB with RF simulator on the host (not inside nws Docker).
# Usage:
#   ./run_gnb_host.sh [path/to/gnb.yaml]
# Example:
#   ./run_gnb_host.sh
#   ./run_gnb_host.sh ../nws/gnb.sa.band78.106prb.rfsim.open5gs.5slices.yaml
#
# For network slicing (SCHE_NS), set dl_scheduler_type/ul_scheduler_type: 1 under MACRLCs in the YAML.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/openairinterface5g/cmake_targets/ran_build/build"
CONFIG="${1:-${SCRIPT_DIR}/gnb.sa.band78.106prb.rfsim.open5gs.5slices.yaml}"

if [[ ! -x "${BUILD_DIR}/nr-softmodem" ]]; then
  echo "Build nr-softmodem first, e.g.:"
  echo "  cd ${REPO_ROOT}/openairinterface5g/cmake_targets && ./build_oai --ninja --gNB --nrUE --build-e2 --build-lib telnetsrv"
  exit 1
fi
if [[ ! -f "${CONFIG}" ]]; then
  echo "Config not found: ${CONFIG}"
  exit 1
fi

export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"
export OAI_GDBSTACKS=1
cd "${BUILD_DIR}"

echo "Starting gNB with: ${CONFIG}"
echo "Add --telnetsrv to enable telnet (e.g. oai sch). Use sudo if TUN creation fails."
exec ./nr-softmodem -O "${CONFIG}" --rfsim -E \
  --log_config.global_log_options level,nocolor,time "$@"

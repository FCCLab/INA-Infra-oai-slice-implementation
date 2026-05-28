#!/bin/bash

set -euo pipefail

CONFIGFILE=/workspace/du.yaml
BUILD_DIR=/workspace/openairinterface5g/cmake_targets/ran_build/build

if [ ! -f "$CONFIGFILE" ]; then
  echo "Error: DU configuration file $CONFIGFILE not found"
  exit 1
fi

if [ ! -d "$BUILD_DIR" ]; then
  echo "Error: Build directory $BUILD_DIR not found. Please build first using ./build.sh"
  exit 1
fi

cd "$BUILD_DIR"

if [ ! -f "./nr-softmodem" ]; then
  echo "Error: nr-softmodem not found in $BUILD_DIR. Please build first using ./build.sh"
  exit 1
fi

args=("./nr-softmodem" "-O" "$CONFIGFILE" "--telnetsrv")

if [[ -v USE_ADDITIONAL_OPTIONS ]]; then
  for word in ${USE_ADDITIONAL_OPTIONS}; do
    args+=("$word")
  done
fi

export OAI_GDBSTACKS=1
export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="/usr/local/lib/flexric:/usr/local/lib:${LD_LIBRARY_PATH}"

FLEXRIC_BUILD_DIR="/workspace/openairinterface5g/openair2/E2AP/flexric/build"
if [ -d "$FLEXRIC_BUILD_DIR" ]; then
  export LD_LIBRARY_PATH="${FLEXRIC_BUILD_DIR}/src/lib:${FLEXRIC_BUILD_DIR}/src/sm:${FLEXRIC_BUILD_DIR}/src/sm/mac_sm:${FLEXRIC_BUILD_DIR}/src/sm/rlc_sm:${FLEXRIC_BUILD_DIR}/src/sm/pdcp_sm:${FLEXRIC_BUILD_DIR}/src/sm/slice_sm:${FLEXRIC_BUILD_DIR}/src/sm/kpm_sm:${FLEXRIC_BUILD_DIR}/src/sm/rc_sm:${FLEXRIC_BUILD_DIR}/src/sm/gtp_sm:${FLEXRIC_BUILD_DIR}/src/sm/tc_sm:${LD_LIBRARY_PATH}"
fi

LOG_FILE="${DU_LOG_FILE:-/workspace/log/du.log}"
mkdir -p "$(dirname "$LOG_FILE")"

USE_GDB=${USE_GDB:-0}
if [ "$USE_GDB" = "1" ] || [ "${1:-}" = "--gdb" ]; then
  exec gdb --args "${args[@]}"
else
  exec "${args[@]}" | tee "$LOG_FILE" 2>&1
fi

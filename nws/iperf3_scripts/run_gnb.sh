#!/bin/bash

set -euo pipefail

# Configuration file path
CONFIGFILE=/workspace/gnb.yaml

# Build directory
BUILD_DIR=/workspace/openairinterface5g/cmake_targets/ran_build/build

# Check if config file exists
if [ ! -f "$CONFIGFILE" ]; then
  echo "Error: Configuration file $CONFIGFILE not found"
  exit 1
fi

# Check if build directory exists
if [ ! -d "$BUILD_DIR" ]; then
  echo "Error: Build directory $BUILD_DIR not found. Please build first using ./build.sh"
  exit 1
fi

# Change to build directory
cd "$BUILD_DIR"

# Check if nr-softmodem exists
if [ ! -f "./nr-softmodem" ]; then
  echo "Error: nr-softmodem not found in $BUILD_DIR. Please build first using ./build.sh"
  exit 1
fi

echo "=================================="
echo "== Starting gNB soft modem"
echo "== Configuration file: $CONFIGFILE"
echo "== Build directory: $BUILD_DIR"
echo "=================================="

# Build command arguments
args=("./nr-softmodem" "-O" "$CONFIGFILE" "--telnetsrv")

# Add additional options from environment variable if set
if [[ -v USE_ADDITIONAL_OPTIONS ]]; then
  echo "== Additional options: ${USE_ADDITIONAL_OPTIONS}"
  # Split USE_ADDITIONAL_OPTIONS by spaces and add to args
  for word in ${USE_ADDITIONAL_OPTIONS}; do
    args+=("$word")
  done
fi

echo "== Command: ${args[@]}"
echo "=================================="

# Enable printing of stack traces on assert
export OAI_GDBSTACKS=1

# Set up LD_LIBRARY_PATH for libraries
# Priority order (first found wins):
# 1. Build directory - contains liboai_device.so (symlink to librfsimulator.so) and other OAI libraries
# 2. FlexRIC build directory - for E2 agent service models (BUILT IN CONTAINER - ABI compatible)
# 3. System FlexRIC directory - for installed service models (BUILT IN CONTAINER - ABI compatible)
export LD_LIBRARY_PATH="${BUILD_DIR}:${LD_LIBRARY_PATH:-}"

FLEXRIC_BUILD_DIR="/workspace/openairinterface5g/openair2/E2AP/flexric/build"
if [ -d "$FLEXRIC_BUILD_DIR" ]; then
    # Add FlexRIC build directories - these libraries are built IN the container, so ABI compatible
    export LD_LIBRARY_PATH="${FLEXRIC_BUILD_DIR}/src/lib:${FLEXRIC_BUILD_DIR}/src/sm:${FLEXRIC_BUILD_DIR}/src/sm/mac_sm:${FLEXRIC_BUILD_DIR}/src/sm/rlc_sm:${FLEXRIC_BUILD_DIR}/src/sm/pdcp_sm:${FLEXRIC_BUILD_DIR}/src/sm/slice_sm:${FLEXRIC_BUILD_DIR}/src/sm/kpm_sm:${FLEXRIC_BUILD_DIR}/src/sm/rc_sm:${FLEXRIC_BUILD_DIR}/src/sm/gtp_sm:${FLEXRIC_BUILD_DIR}/src/sm/tc_sm:${LD_LIBRARY_PATH}"
    echo "== Using FlexRIC libraries from build directory (built in container - ABI compatible)"
fi

# Add system directories (libraries installed by build_oai.sh inside container)
export LD_LIBRARY_PATH="/usr/local/lib/flexric:/usr/local/lib:${LD_LIBRARY_PATH}"

echo "== LD_LIBRARY_PATH includes:"
echo "==   - Build directory (for liboai_device.so/rfsimulator)"
echo "==   - FlexRIC build directory (for E2 agent libraries - BUILT IN CONTAINER)"
echo "==   - /usr/local/lib/flexric (for installed service models - BUILT IN CONTAINER)"

# Log file path. Default to the bind-mounted host directory under `nws/log/gnb.log`.
LOG_FILE="${GNB_LOG_FILE:-/workspace/log/gnb.log}"

mkdir -p "$(dirname "$LOG_FILE")"

echo "== Output will be logged to: $LOG_FILE"
echo "=================================="

USE_GDB=0

# Check if GDB is requested via environment variable or argument
USE_GDB=${USE_GDB:-0}
if [ "$USE_GDB" = "1" ] || [ "${1:-}" = "--gdb" ]; then
    echo "== Running with GDB for debugging"
    echo "== To debug, use: docker exec -it nws-oai-gnb gdb --args ${args[@]}"
    echo "== Or set USE_GDB=1 environment variable"
    
    # Check if gdb is available
    if ! command -v gdb &> /dev/null; then
        echo "Error: gdb not found. Please install gdb in the container."
        exit 1
    fi
    
    # Run with gdb, redirecting output to log file
    exec gdb --args "${args[@]}"
else
    # Execute the command normally, redirecting output to log file
    exec "${args[@]}" | tee "$LOG_FILE" 2>&1
fi

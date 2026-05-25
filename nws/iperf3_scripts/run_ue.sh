#!/bin/bash

set -euo pipefail

# Configuration file path (optional for UE)
CONFIGFILE=/workspace/nr-ue.yaml

# Build directory
BUILD_DIR=/workspace/openairinterface5g/cmake_targets/ran_build/build

# Check if build directory exists
if [ ! -d "$BUILD_DIR" ]; then
  echo "Error: Build directory $BUILD_DIR not found. Please build first using ./build.sh"
  exit 1
fi

# Change to build directory
cd "$BUILD_DIR"

# So dlopen() finds libparams_yaml.so (built in this dir); without it, YAML config fails and UE asserts
# (e.g. extra_pdu_id vs default_pdu_session_id). Same idea as run_gnb.sh.
export LD_LIBRARY_PATH="${BUILD_DIR}:/usr/local/lib:${LD_LIBRARY_PATH:-}"

# Check if nr-uesoftmodem exists
if [ ! -f "./nr-uesoftmodem" ]; then
  echo "Error: nr-uesoftmodem not found in $BUILD_DIR. Please build first using ./build.sh"
  exit 1
fi

echo "=================================="
echo "== Starting NR UE soft modem"
if [ -f "$CONFIGFILE" ]; then
  echo "== Configuration file: $CONFIGFILE"
else
  echo "== No configuration file (using command line options only)"
fi
echo "== Build directory: $BUILD_DIR"
echo "=================================="

# Build command arguments
args=("./nr-uesoftmodem")

# Add config file if it exists
if [ -f "$CONFIGFILE" ]; then
  args+=("-O" "$CONFIGFILE")
  # Add rfsim flag if config file exists (rfsimulator section in config)
  args+=("--rfsim")
  # Enable channel modeling if channelmod section exists in config
  if grep -q "channelmod:" "$CONFIGFILE" 2>/dev/null; then
    args+=("--rfsimulator.options" "chanmod")
  fi
  # Add required command-line options that may not be in config
  args+=("-E" "-r" "106" "--numerology" "1" "-C" "3319680000")
fi

echo "== Command: ${args[@]}"
echo "=================================="

# Enable printing of stack traces on assert
export OAI_GDBSTACKS=1

# Execute the command
exec "${args[@]}"

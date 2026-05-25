#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}"/../
OAI_SOURCE_DIR="${WORKSPACE_DIR}/openairinterface5g"

# Build OAI with E2 agent support and telnet server
cd $OAI_SOURCE_DIR/cmake_targets
./build_oai --ninja --gNB --nrUE --build-e2 --build-lib telnetsrv

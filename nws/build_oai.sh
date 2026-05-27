#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}"/../
OAI_SOURCE_DIR="${WORKSPACE_DIR}/openairinterface5g"
BUILD_DIR="${OAI_SOURCE_DIR}/cmake_targets/ran_build/build"

# Match FlexRIC nearRT-RIC / xApp (see nws/build_flexric.sh and docker-compose E2AP_VERSION).
E2AP_VERSION="${E2AP_VERSION:-E2AP_V3}"
KPM_VERSION="${KPM_VERSION:-KPM_V3_00}"
CLEAN="${CLEAN:-0}"

if [[ "${CLEAN}" == "1" ]]; then
  echo "== Removing OAI cmake build cache (${BUILD_DIR})"
  rm -rf "${BUILD_DIR}"
fi

echo "== OAI E2 build: E2AP_VERSION=${E2AP_VERSION} KPM_VERSION=${KPM_VERSION}"

cd "${OAI_SOURCE_DIR}/cmake_targets"
./build_oai --ninja --gNB --nrUE --build-e2 --build-lib telnetsrv \
  --cmake-opt "-DE2AP_VERSION=${E2AP_VERSION} -DKPM_VERSION=${KPM_VERSION}"

#!/bin/bash
# Build FlexRIC (nearRT-RIC + service models) from the OAI submodule.
# Sources: openairinterface5g/openair2/E2AP/flexric (FCCLab/flexric-fcc).
#
# Usage (from nws/ or inside the OAI dev container at /workspace):
#   ./build_flexric.sh
#   E2AP_VERSION=E2AP_V3 KPM_VERSION=KPM_V3_00 ./build_flexric.sh
#   INSTALL=0 ./build_flexric.sh          # skip sudo make install
#   CLEAN=1 ./build_flexric.sh            # remove build/ before configuring
#   RUN_TESTS=1 ./build_flexric.sh        # run ctest after build
#   INSTALL_SWIG=0 ./build_flexric.sh         # do not auto-install SWIG from source
#   INSTALL_PYTHON=0 ./build_flexric.sh       # skip apt python3-dev install
#   XAPP_MULTILANGUAGE=OFF ./build_flexric.sh # nearRT-RIC only, no Python xApps

set -euo pipefail

flexric_swig_ok() {
  command -v swig >/dev/null 2>&1 || return 1
  local ver
  ver="$(swig -version 2>/dev/null | sed -n 's/^SWIG Version \([0-9.]*\).*/\1/p' | head -1)"
  [[ -n "${ver}" ]] || return 1
  awk -v v="${ver}" 'BEGIN { exit !(v >= 4.1) }'
}

# Ubuntu 22.04 apt ships SWIG 4.0.x; FlexRIC needs >= 4.1 (same build as nws/Dockerfile).
flexric_install_swig() {
  flexric_swig_ok && return 0
  [[ "${INSTALL_SWIG:-1}" == "1" ]] || return 1

  if ! command -v sudo >/dev/null 2>&1; then
    echo "error: sudo required to install SWIG (or set INSTALL_SWIG=0 and XAPP_MULTILANGUAGE=OFF)" >&2
    return 1
  fi

  local swig_src="${SWIG_SRC_DIR:-${WORKSPACE_DIR:-/tmp}/.cache/flexric-swig-src}"
  echo "== Installing SWIG >= 4.1 from source =="
  echo "   source dir: ${swig_src}"

  echo "== Installing SWIG build dependencies (apt) =="
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git automake libtool bison byacc libpcre2-dev

  mkdir -p "$(dirname "${swig_src}")"
  if [[ ! -d "${swig_src}/.git" ]]; then
    rm -rf "${swig_src}"
    git clone --depth 1 --branch release-4.1 https://github.com/swig/swig.git "${swig_src}"
  fi

  pushd "${swig_src}" >/dev/null
  ./autogen.sh
  ./configure --prefix=/usr/local
  make -j"${JOBS:-$(nproc)}"
  sudo make install
  sudo ldconfig
  popd >/dev/null

  hash -r 2>/dev/null || true
  if flexric_swig_ok; then
    echo "== SWIG installed: $(swig -version 2>&1 | head -1) =="
    return 0
  fi
  echo "error: SWIG install finished but 'swig' is still missing or < 4.1" >&2
  return 1
}

flexric_python_ok() {
  command -v python3 >/dev/null 2>&1 || return 1
  python3-config --includes >/dev/null 2>&1 || return 1
  return 0
}

flexric_install_python_deps() {
  [[ "${XAPP_MULTILANGUAGE:-ON}" == "ON" ]] || return 0
  [[ "${INSTALL_PYTHON:-1}" == "1" ]] || return 1
  flexric_python_ok && return 0

  if ! command -v sudo >/dev/null 2>&1; then
    echo "error: sudo required to install python3-dev" >&2
    return 1
  fi

  echo "== Installing Python 3 dev packages (apt) =="
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-dev

  flexric_python_ok
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${SCRIPT_DIR}/.."
FLEXRIC_DIR="${WORKSPACE_DIR}/openairinterface5g/openair2/E2AP/flexric"
BUILD_DIR="${FLEXRIC_DIR}/build"

E2AP_VERSION="${E2AP_VERSION:-E2AP_V3}"
KPM_VERSION="${KPM_VERSION:-KPM_V3_00}"
INSTALL="${INSTALL:-1}"
CLEAN="${CLEAN:-0}"
RUN_TESTS="${RUN_TESTS:-0}"
JOBS="${JOBS:-$(nproc)}"
INSTALL_SWIG="${INSTALL_SWIG:-1}"
INSTALL_PYTHON="${INSTALL_PYTHON:-1}"
# Python xApp bindings (SWIG + Python3); set OFF to build nearRT-RIC only.
XAPP_MULTILANGUAGE="${XAPP_MULTILANGUAGE:-ON}"

if [[ ! -f "${FLEXRIC_DIR}/CMakeLists.txt" ]]; then
  echo "error: FlexRIC sources not found at ${FLEXRIC_DIR}" >&2
  echo "  Initialize the submodule from openairinterface5g:" >&2
  echo "    cd ${WORKSPACE_DIR}/openairinterface5g" >&2
  echo "    git submodule update --init openair2/E2AP/flexric" >&2
  exit 1
fi

if [[ "${XAPP_MULTILANGUAGE}" == "ON" ]]; then
  flexric_install_python_deps || true
  if ! flexric_swig_ok; then
    flexric_install_swig || true
  fi
fi

# Prefer gcc-12/g++-12 when both exist (FlexRIC does not support gcc-11).
# Override anytime: CC=gcc CXX=g++ ./build_flexric.sh
if [[ -z "${CC:-}" && -z "${CXX:-}" ]]; then
  if command -v gcc-12 >/dev/null 2>&1 && command -v g++-12 >/dev/null 2>&1; then
    export CC=gcc-12
    export CXX=g++-12
  else
    export CC="${CC:-gcc}"
    export CXX="${CXX:-g++}"
    if gcc --version 2>/dev/null | grep -q ' 11\.'; then
      echo "warning: default compiler is gcc-11; FlexRIC recommends gcc-12/13." >&2
      echo "         Install: sudo apt install g++-12   or use the OAI Docker image." >&2
    fi
  fi
fi

echo "== FlexRIC build =="
echo "   dir:          ${FLEXRIC_DIR}"
echo "   CC/CXX:       ${CC:-unset} / ${CXX:-unset}"
echo "   E2AP_VERSION: ${E2AP_VERSION}"
echo "   KPM_VERSION:  ${KPM_VERSION}"
echo "   jobs:         ${JOBS}"
echo "   install:      ${INSTALL}"
echo "   clean:        ${CLEAN}"
echo "   XAPP_MULTILANGUAGE: ${XAPP_MULTILANGUAGE}"

if [[ "${XAPP_MULTILANGUAGE}" == "ON" ]]; then
  if ! flexric_python_ok; then
    echo "error: Python 3 dev headers missing (need python3-dev; INSTALL_PYTHON=1 failed or was skipped)." >&2
    exit 1
  fi
  if ! flexric_swig_ok; then
    echo "error: XAPP_MULTILANGUAGE=ON requires SWIG >= 4.1 (INSTALL_SWIG=1 failed or was skipped)." >&2
    exit 1
  fi
  echo "   Python:       $(python3 --version 2>&1)"
  echo "   SWIG:         $(swig -version 2>&1 | head -1)"
fi

if [[ "${CLEAN}" == "1" ]]; then
  rm -rf "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

CMAKE_ARGS=(
  -DE2AP_VERSION="${E2AP_VERSION}"
  -DKPM_VERSION="${KPM_VERSION}"
  -DXAPP_MULTILANGUAGE="${XAPP_MULTILANGUAGE}"
)
if [[ "${XAPP_MULTILANGUAGE}" == "ON" ]] && command -v python3 >/dev/null 2>&1; then
  CMAKE_ARGS+=(-DPython3_EXECUTABLE="$(command -v python3)")
fi
cmake .. "${CMAKE_ARGS[@]}"

make -j"${JOBS}"

if [[ "${INSTALL}" == "1" ]]; then
  echo "== Installing service models to /usr/local/lib/flexric =="
  sudo make install
fi

if [[ "${RUN_TESTS}" == "1" ]]; then
  echo "== Running FlexRIC tests =="
  ctest -j"${JOBS}" --output-on-failure
fi

NEAR_RIC="${BUILD_DIR}/examples/ric/nearRT-RIC"
if [[ -x "${NEAR_RIC}" ]]; then
  echo "== Build OK: ${NEAR_RIC}"
else
  echo "error: nearRT-RIC binary not found at ${NEAR_RIC}" >&2
  exit 1
fi

if [[ "${XAPP_MULTILANGUAGE}" == "ON" ]]; then
  PY_SDK_SO="${BUILD_DIR}/examples/xApp/python3/_xapp_sdk.so"
  PY_SDK_PY="${BUILD_DIR}/examples/xApp/python3/xapp_sdk.py"
  if [[ -f "${PY_SDK_SO}" && -f "${PY_SDK_PY}" ]]; then
    echo "== Python xApp SDK: ${PY_SDK_PY}"
  else
    echo "warning: Python xApp SDK not found under build/examples/xApp/python3/" >&2
  fi
fi

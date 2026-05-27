#!/bin/bash
# Start nearRT-RIC for nws Docker stacks.
# The openairinterface5g tree is bind-mounted over /workspace/openairinterface5g, so the
# FlexRIC build inside the image may be absent; use the host build when present.

set -euo pipefail

CONFIGFILE="${FLEXRIC_CONF:-/workspace/flexric.conf}"
FLEXRIC_ROOT=/workspace/openairinterface5g/openair2/E2AP/flexric
BUILD_DIR="${FLEXRIC_ROOT}/build"
PLUGIN_STAGING="${BUILD_DIR}/flexric_plugins"
INSTALLED_RIC=/usr/local/bin/nearRT-RIC
INSTALLED_LIBS=/usr/local/lib/flexric

flexric_stage_build_plugins() {
  mkdir -p "${PLUGIN_STAGING}"
  shopt -s nullglob
  local sm
  for sm in "${BUILD_DIR}"/src/sm/*/lib*_sm.so; do
    ln -sf "$(realpath "${sm}")" "${PLUGIN_STAGING}/$(basename "${sm}")"
  done
  for sm in "${BUILD_DIR}"/src/sm/kpm_sm/*/libkpm_sm.so; do
    ln -sf "$(realpath "${sm}")" "${PLUGIN_STAGING}/$(basename "${sm}")"
  done
  shopt -u nullglob
}

flexric_plugin_dir_ok() {
  local dir="$1"
  [[ -d "${dir}" ]] && compgen -G "${dir}/lib*_sm.so" >/dev/null
}

NEAR_RIC=""
LIBS_DIR=""

if [[ -x "${BUILD_DIR}/examples/ric/nearRT-RIC" ]]; then
  NEAR_RIC="${BUILD_DIR}/examples/ric/nearRT-RIC"
  flexric_stage_build_plugins
  if flexric_plugin_dir_ok "${PLUGIN_STAGING}"; then
    LIBS_DIR="${PLUGIN_STAGING}"
  elif flexric_plugin_dir_ok "${INSTALLED_LIBS}"; then
    echo "warning: using ${INSTALLED_LIBS} plugins with host-built nearRT-RIC (run nws/build_flexric.sh if SM load fails)" >&2
    LIBS_DIR="${INSTALLED_LIBS}"
  fi
elif [[ -x "${INSTALLED_RIC}" ]]; then
  NEAR_RIC="${INSTALLED_RIC}"
  LIBS_DIR="${INSTALLED_LIBS}"
fi

if [[ -z "${NEAR_RIC}" ]]; then
  echo "error: nearRT-RIC not found." >&2
  echo "  Build on the host (bind-mounted tree):" >&2
  echo "    cd /workspace/nws && ./build_flexric.sh" >&2
  echo "  or rebuild the oai-flexric image (FlexRIC is built at image build time)." >&2
  exit 1
fi

if [[ -z "${LIBS_DIR}" ]] || ! flexric_plugin_dir_ok "${LIBS_DIR}"; then
  echo "error: no Slice/MAC/KPM service model libraries found." >&2
  echo "  Run: cd /workspace/nws && INSTALL=1 ./build_flexric.sh" >&2
  exit 1
fi

if [[ ! -f "${CONFIGFILE}" ]]; then
  echo "error: config not found: ${CONFIGFILE}" >&2
  exit 1
fi

# Trailing slash required by FlexRIC plugin loader.
case "${LIBS_DIR}" in
  */) ;;
  *) LIBS_DIR="${LIBS_DIR}/" ;;
esac

export LD_LIBRARY_PATH="${LIBS_DIR}:${BUILD_DIR}/src/xApp:/usr/local/lib/flexric:/usr/local/lib:${LD_LIBRARY_PATH:-}"

echo "Starting nearRT-RIC: ${NEAR_RIC}"
echo "  config:  ${CONFIGFILE}"
echo "  plugins: ${LIBS_DIR}"
echo "  E2 SCTP: port 36421 (gNB e2_agent -> NEAR_RIC_IP in flexric.conf)"
echo ""
echo "After startup you should see FlexRIC lines such as:"
echo "  [NEAR-RIC]: nearRT-RIC IP Address = ..."
echo "  [NEAR-RIC]: Loading SM ID = ..."
echo "When the gNB E2 agent connects:"
echo "  [E2AP]: E2 SETUP-REQUEST rx from PLMN ..."
echo "(There is no literal 'gNB connected' log.)"
echo "If the gNB started before the RIC, restart it: docker restart nws-oai-gnb"
echo ""

# Line-buffer stdout/stderr so docker logs show FlexRIC output immediately.
if command -v stdbuf >/dev/null 2>&1; then
  exec stdbuf -oL -eL "${NEAR_RIC}" -c "${CONFIGFILE}" -p "${LIBS_DIR}"
else
  exec "${NEAR_RIC}" -c "${CONFIGFILE}" -p "${LIBS_DIR}"
fi

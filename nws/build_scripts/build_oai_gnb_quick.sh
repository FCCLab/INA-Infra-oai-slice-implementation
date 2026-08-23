#!/bin/bash
# Package oai-gnb from a local cmake build (no docker build_oai / ran-build recompile).
# Also runs incremental FlexRIC quick build (slice_sm etc.) unless --no-flexric.
# Requires: ran-base:latest; staging/flexric from build_oai_flexric_quick.sh.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"
LOCAL_BUILD="${OAI_DIR}/cmake_targets/ran_build/build"
LOCAL_BIN="${LOCAL_BUILD}/nr-softmodem"
STAGING_DIR="${SCRIPT_DIR}/staging"
STAGED_BIN="${STAGING_DIR}/nr-softmodem"
STAGED_UE_BIN="${STAGING_DIR}/nr-uesoftmodem"
STAGED_LIBS="${STAGING_DIR}/libs"
STAGED_FLEXRIC="${STAGING_DIR}/flexric/lib/flexric"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.gNB.quick.ubuntu"
UE_QUICK_SH="${SCRIPT_DIR}/build_oai_nr_ue_quick.sh"
FLEXRIC_QUICK_SH="${SCRIPT_DIR}/build_oai_flexric_quick.sh"
NO_CACHE=0
BUILD_LOCAL=1
BUILD_FLEXRIC=1

# gNB + UE must share librfsimulator (RFsim wire protocol).
QUICK_CMAKE_TARGETS=(nr-softmodem nr-uesoftmodem rfsimulator)

usage() {
    echo "Usage: $0 [--no-cache] [--no-local-build] [--no-flexric] [--flexric-only]"
    echo "  --no-cache         docker build --no-cache for oai-gnb packaging layer"
    echo "  --no-local-build   skip cmake build (nr-softmodem + runtime .so must already exist)"
    echo "  --no-flexric       skip incremental FlexRIC build (use existing staging/flexric)"
    echo "  --flexric-only     only run build_oai_flexric_quick.sh"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        --no-local-build)
            BUILD_LOCAL=0
            shift
            ;;
        --no-flexric)
            BUILD_FLEXRIC=0
            shift
            ;;
        --flexric-only)
            BUILD_FLEXRIC=1
            BUILD_LOCAL=0
            shift
            if [[ $# -gt 0 ]]; then
                echo "Unknown option after --flexric-only: $1" >&2
                usage >&2
                exit 1
            fi
            bash "${FLEXRIC_QUICK_SH}" $([[ "${NO_CACHE}" -eq 1 ]] && echo --no-cache)
            exit 0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

if ! docker image inspect ran-base:latest &>/dev/null; then
    echo "error: ran-base:latest missing — run build_ran_base.sh once" >&2
    exit 1
fi

if [[ "${BUILD_FLEXRIC}" -eq 1 ]]; then
  FLEXRIC_ARGS=()
  [[ "${NO_CACHE}" -eq 1 ]] && FLEXRIC_ARGS+=(--no-cache)
  [[ "${BUILD_LOCAL}" -eq 0 ]] && FLEXRIC_ARGS+=(--no-local-build)
  echo "Running incremental FlexRIC quick build..."
  bash "${FLEXRIC_QUICK_SH}" "${FLEXRIC_ARGS[@]}"
fi

if [[ ! -f "${STAGED_FLEXRIC}/libslice_sm.so" ]]; then
    echo "error: ${STAGED_FLEXRIC}/libslice_sm.so missing — run build_oai_flexric_quick.sh" >&2
    exit 1
fi

if [[ ! -d "${LOCAL_BUILD}" ]]; then
    echo "error: ${LOCAL_BUILD} missing — run a cmake configure first (build_ran_build.sh or build_oai)" >&2
    exit 1
fi

if [[ "${BUILD_LOCAL}" -eq 1 ]]; then
    echo "Building local targets: ${QUICK_CMAKE_TARGETS[*]}..."
    cmake --build "${LOCAL_BUILD}" --target "${QUICK_CMAKE_TARGETS[@]}" -j"$(nproc)"
fi

if [[ ! -f "${LOCAL_BIN}" ]]; then
    echo "error: ${LOCAL_BIN} not found" >&2
    exit 1
fi

mkdir -p "${STAGING_DIR}" "${STAGED_LIBS}"
rm -f "${STAGED_LIBS}"/*.so
cp -f "${LOCAL_BIN}" "${STAGED_BIN}"
if [[ -f "${LOCAL_BUILD}/nr-uesoftmodem" ]]; then
    cp -f "${LOCAL_BUILD}/nr-uesoftmodem" "${STAGED_UE_BIN}"
fi
cp -f "${LOCAL_BUILD}"/*.so "${STAGED_LIBS}/"

if [[ ! -f "${STAGED_LIBS}/librfsimulator.so" ]] || [[ ! -f "${STAGED_LIBS}/libparams_yaml.so" ]]; then
    echo "error: staged libs missing (need librfsimulator.so + libparams_yaml.so)" >&2
    exit 1
fi

echo "Quick packaging oai-gnb:latest (local nr-softmodem + staged FlexRIC SM plugins)..."
cd "${WORKSPACE_DIR}"

BUILD_ARGS=(
    --target oai-gnb
    --tag oai-gnb:latest
    --file "${DOCKERFILE}"
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .

docker tag oai-gnb:latest oai-cucp:latest
docker tag oai-gnb:latest oai-du:latest
docker tag oai-gnb:latest oai-gnb:latest-"${ARCH_TAG}"
docker tag oai-cucp:latest oai-cucp:latest-"${ARCH_TAG}"
docker tag oai-du:latest oai-du:latest-"${ARCH_TAG}"

echo "Successfully quick-built oai-gnb from ${LOCAL_BIN} (${ARCH_TAG})"

if [[ -f "${STAGED_UE_BIN}" ]] && [[ -f "${UE_QUICK_SH}" ]]; then
    bash "${UE_QUICK_SH}" $([[ "${NO_CACHE}" -eq 1 ]] && echo --no-cache)
else
    echo "warning: nr-uesoftmodem not staged — UE image not updated (RFsim may mismatch gNB)" >&2
fi

echo "Done. Restart nws-nearRT-RIC and nws-oai-gnb to pick up new images."

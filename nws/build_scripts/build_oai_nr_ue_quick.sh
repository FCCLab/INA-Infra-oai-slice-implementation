#!/bin/bash
# Package oai-nr-ue from local cmake build (must match gNB quick / same librfsimulator).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"
LOCAL_BUILD="${OAI_DIR}/cmake_targets/ran_build/build"
LOCAL_BIN="${LOCAL_BUILD}/nr-uesoftmodem"
STAGED_BIN="${SCRIPT_DIR}/staging/nr-uesoftmodem"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.nrUE.quick.ubuntu"
NO_CACHE=0

usage() {
    echo "Usage: $0 [--no-cache]"
    echo "  Run build_oai_gnb_quick.sh first (shared staging/libs + nr-softmodem)."
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE=1
            shift
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

if [[ ! -f "${STAGED_BIN}" ]]; then
    echo "error: ${STAGED_BIN} missing — run build_oai_gnb_quick.sh first" >&2
    exit 1
fi

ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

echo "Quick packaging oai-nr-ue:latest from ${STAGED_BIN}..."
cd "${WORKSPACE_DIR}"

BUILD_ARGS=(
    --target oai-nr-ue
    --tag oai-nr-ue:latest
    --file "${DOCKERFILE}"
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .
docker tag oai-nr-ue:latest oai-nr-ue:latest-"${ARCH_TAG}"
echo "Successfully quick-built oai-nr-ue from ${LOCAL_BIN} (${ARCH_TAG})"

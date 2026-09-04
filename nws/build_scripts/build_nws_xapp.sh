#!/bin/bash
# Package nws-xapp:latest (FlexRIC Python slice xApp + REST :18080).
#
# Requires oai-flexric:latest built with -DXAPP_MULTILANGUAGE=ON so
# /usr/local/flexric/xApp/python3/xapp_sdk.py exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
XAPP_DIR="${WORKSPACE_DIR}/nws/scripts/xapp"
FLEXRIC_BUILD_SH="${SCRIPT_DIR}/build_oai_flexric.sh"

FLEXRIC_IMAGE="${FLEXRIC_IMAGE:-oai-flexric:latest}"
XAPP_IMAGE="${XAPP_IMAGE:-nws-xapp:latest}"

# Avoid NVIDIA default runtime while Aerial L1 holds the GPUs.
export NVIDIA_VISIBLE_DEVICES="${NVIDIA_VISIBLE_DEVICES:-void}"
export DOCKER_DEFAULT_RUNTIME="${DOCKER_DEFAULT_RUNTIME:-runc}"

NO_CACHE=0
WITH_FLEXRIC=0

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Build ${XAPP_IMAGE} from nws/scripts/xapp (FROM ${FLEXRIC_IMAGE}).

Does not restart L1/gNB. After a successful build:

  docker compose -f oai-nvidia/sa_gnb_aerial/dgx2/dgx2_sera42/docker-compose.local_open5gs.5slices.e2ap.yaml \\
    up -d --no-deps nws-xapp-slice-monitor

Options:
  --with-flexric   Run build_oai_flexric.sh first (needed if xapp_sdk.py is missing)
  --no-cache       Pass docker build --no-cache
  -h, --help       Show this help

Environment:
  FLEXRIC_IMAGE    Base image (default: oai-flexric:latest)
  XAPP_IMAGE       Output image (default: nws-xapp:latest)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-flexric)
            WITH_FLEXRIC=1
            shift
            ;;
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

ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

if [[ ! -f "${XAPP_DIR}/Dockerfile" ]]; then
    echo "error: xApp Dockerfile missing: ${XAPP_DIR}/Dockerfile" >&2
    exit 1
fi

docker_run_runc() {
    docker run --rm --runtime=runc \
        -e NVIDIA_VISIBLE_DEVICES=void \
        --network none \
        "$@"
}

flexric_has_sdk() {
    docker_run_runc --entrypoint sh "${FLEXRIC_IMAGE}" \
        -c 'test -f /usr/local/flexric/xApp/python3/xapp_sdk.py'
}

if [[ "${WITH_FLEXRIC}" -eq 1 ]]; then
    echo "Building ${FLEXRIC_IMAGE} (XAPP_MULTILANGUAGE)..."
    bash "${FLEXRIC_BUILD_SH}"
fi

if ! docker image inspect "${FLEXRIC_IMAGE}" &>/dev/null; then
    echo "error: ${FLEXRIC_IMAGE} not found." >&2
    echo "  bash ${FLEXRIC_BUILD_SH}" >&2
    echo "  # or: $0 --with-flexric" >&2
    exit 1
fi

if ! flexric_has_sdk; then
    echo "error: ${FLEXRIC_IMAGE} has no Python xapp_sdk (XAPP_MULTILANGUAGE was OFF)." >&2
    echo "  Rebuild FlexRIC then this image:" >&2
    echo "    $0 --with-flexric" >&2
    exit 1
fi

echo "Building ${XAPP_IMAGE} from ${XAPP_DIR} (base ${FLEXRIC_IMAGE})..."
BUILD_ARGS=(
    --tag "${XAPP_IMAGE}"
    --build-arg "FLEXRIC_IMAGE=${FLEXRIC_IMAGE}"
    --file "${XAPP_DIR}/Dockerfile"
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" "${XAPP_DIR}"

docker tag "${XAPP_IMAGE}" "${XAPP_IMAGE}-${ARCH_TAG}"
echo "Successfully built and tagged ${XAPP_IMAGE} (${ARCH_TAG})"
